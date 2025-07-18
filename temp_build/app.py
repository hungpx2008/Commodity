from flask import Flask, render_template, request, send_file, jsonify
import pandas as pd
from openai import OpenAI
import os
import json
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.feature_extraction.text import CountVectorizer
import concurrent.futures
import threading
import time

app = Flask(__name__)

# Global variables to store the current processing task and cancellation flag
current_task = None
upload_cancelled = False
openai_api_error = False

# Configure OpenRouter API
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key="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",  # Ensure to keep your API key secure
)

# Config file path
REFERENCE_PATH = "config/reference.json"

# Load references from file
def load_reference_list():
    try:
        with open(REFERENCE_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Could not load reference list: {e}")
        return ["FLUID SEAMLESS STEEL"]

# Save references to file
def save_reference_list(reference_list):
    os.makedirs(os.path.dirname(REFERENCE_PATH), exist_ok=True)
    with open(REFERENCE_PATH, "w") as f:
        json.dump(reference_list, f, indent=2)

# Load global reference list
REFERENCE_COMMODITY_LIST = load_reference_list()

# Cosine similarity check
def check_cosine_similarity(commodity):
    for ref in REFERENCE_COMMODITY_LIST:
        vectorizer = CountVectorizer().fit_transform([commodity, ref])
        cosine_sim = cosine_similarity(vectorizer[0:1], vectorizer[1:2])
        if cosine_sim[0][0] > 0.1:
            return 'Y'
    return 'N'

# LLM prompt using OR logic
def check_commodity_from_llm(commodity):
    global openai_api_error  # To track if API call fails
    try:
        reference_string = ', or '.join(f"'{ref}'" for ref in REFERENCE_COMMODITY_LIST)
        prompt = (f"You are an expert in commodities. Your task is to determine if a commodity matches any of the following references: "
            f"Does the phrase '{commodity}' mean the same as the following: "
            f"{reference_string}? Only reply 'Y' for yes or 'N' for no."
        )
        
        completion = client.chat.completions.create(
            #model="openai/gpt-4o-mini-2024-07-18",
            model="deepseek/deepseek-r1-0528:free",
            messages=[{"role": "user", "content": prompt}],
        )
        result = completion.choices[0].message.content.strip()
        return 'Y' if result == 'Y' else 'N'
    except Exception as e:
        if 'Rate limit exceeded' in str(e):
            openai_api_error = True  # Set flag when API rate limit error occurs
        print(f"Error in LLM: {e}")
        return 'N'

# Combine cosine and LLM with fallback to Cosine if LLM fails
def check_commodity_and_cosine(commodity):
    global openai_api_error
    if openai_api_error:
        # Only use Cosine similarity if API call fails
        cosine_result = check_cosine_similarity(commodity)
        return cosine_result, 'N'
    else:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_cosine = executor.submit(check_cosine_similarity, commodity)
            future_llm = executor.submit(check_commodity_from_llm, commodity)
            try:
                return future_cosine.result(), future_llm.result()
            except:
                return future_cosine.result(), 'N'

# Process uploaded Excel
def process_file(file_path):
    try:
        # Đọc toàn bộ file Excel, giữ nguyên tất cả các cột
        df = pd.read_excel(file_path)
        
        # Kiểm tra xem có cột COMMODITY không
        if 'COMMODITY' not in df.columns:
            raise ValueError("File Excel phải có cột 'COMMODITY'")
        
        # Áp dụng logic xử lý cho cột COMMODITY và tạo 2 cột mới
        commodity_results = df['COMMODITY'].apply(lambda x: pd.Series(check_commodity_and_cosine(x)))
        df['Cosine_Similarity'] = commodity_results[0]
        df['LLM_Result'] = commodity_results[1]
        
        # Tạo cột Final_Match dựa trên logic OR
        df['Final_Match'] = df.apply(
            lambda row: 'Y' if row['Cosine_Similarity'] == 'Y' or row['LLM_Result'] == 'Y' else 'N', 
            axis=1
        )
        
        # Lưu file với tên mới
        output_file = "processed_file_with_final_match.xlsx"
        df.to_excel(output_file, index=False)
        return output_file
    except Exception as e:
        print(f"Error processing file: {e}")
        raise

@app.route('/')
def home():
    return render_template('index.html', references=REFERENCE_COMMODITY_LIST)

# Simulate a long-running task (file processing)
def long_running_task(file):
    global current_task, upload_cancelled
    try:
        # Simulate file processing by sleeping for a few seconds
        for i in range(10):  # Simulating task (e.g., processing a file)
            if upload_cancelled:  # If the task is cancelled, stop the processing
                break
            time.sleep(1)  # Simulate processing
            print(f"Processing {file}... {i + 1}/10")
        
        if not upload_cancelled:
            print(f"File {file} processed successfully!")
    finally:
        current_task = None  # Reset the task when it's done

# Upload route
@app.route('/upload', methods=['POST'])
def upload_file():
    global current_task, upload_cancelled, openai_api_error

    try:
        file = request.files['file']
        if not file or file.filename == '':
            return "No file uploaded", 400

        upload_dir = 'uploads'
        os.makedirs(upload_dir, exist_ok=True)
        file_path = os.path.join(upload_dir, file.filename)
        file.save(file_path)

        # Reset the error flag
        openai_api_error = False

        # Start the long-running task in a new thread
        upload_cancelled = False
        current_task = threading.Thread(target=long_running_task, args=(file.filename,))
        current_task.start()

        # Process the file and return the processed file as an Excel download
        processed_path = process_file(file_path)

        # Notify the user if the OpenAI API was not used (429 error)
        if openai_api_error:
            return send_file(processed_path, as_attachment=True, download_name="processed_file_with_cosine_only.xlsx")

        return send_file(processed_path, as_attachment=True)

    except Exception as e:
        return str(e), 500

# Cancel upload route
@app.route('/cancel-upload', methods=['POST'])
def cancel_upload():
    global current_task, upload_cancelled

    if current_task is not None:
        # Set the cancel flag to True
        upload_cancelled = True
        # Wait for the task to cleanly exit
        current_task.join()
        return jsonify({"message": "Process has been cancelled."}), 200
    else:
        return jsonify({"error": "No ongoing process to cancel."}), 400

# Status route to check the process
@app.route('/status', methods=['GET'])
def status():
    if current_task is not None:
        return jsonify({"status": "Processing in progress"}), 200
    else:
        return jsonify({"status": "No process running"}), 200

# Set reference route
@app.route('/set-reference', methods=['POST'])
def set_reference():
    global REFERENCE_COMMODITY_LIST
    try:
        reference_raw = request.form.get('reference') or request.json.get('reference')
        if not reference_raw:
            return "Missing reference input", 400

        REFERENCE_COMMODITY_LIST = [r.strip() for r in reference_raw.split(',') if r.strip()]
        save_reference_list(REFERENCE_COMMODITY_LIST)
        return render_template('index.html', references=REFERENCE_COMMODITY_LIST)
    except Exception as e:
        return str(e), 500

if __name__ == "__main__":
    # Ensure the uploads directory exists
    if not os.path.exists('uploads'):
        os.makedirs('uploads')

    app.run(debug=True)