from flask import Flask, render_template, request, send_file
import pandas as pd
from openai import OpenAI
import os
import json
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.feature_extraction.text import CountVectorizer
import concurrent.futures

app = Flask(__name__)

# Configure OpenRouter API
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key="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",
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
        if cosine_sim[0][0] > 0.2:
            return 'Y'
    return 'N'

# LLM prompt using OR logic
def check_commodity_from_llm(commodity):
    try:
        reference_string = ', or '.join(f"'{ref}'" for ref in REFERENCE_COMMODITY_LIST)
        prompt = (
            f"Does the phrase '{commodity}' mean the same as or something similar to any of the following: "
            f"{reference_string}? Only reply 'Y' for yes or 'N' for no."
        )
        completion = client.chat.completions.create(
            model="deepseek/deepseek-r1-0528-qwen3-8b:free",
            messages=[{"role": "user", "content": prompt}],
        )
        result = completion.choices[0].message.content.strip()
        return 'Y' if result == 'Y' else 'N'
    except Exception as e:
        print(f"Error in LLM: {e}")
        return 'N'

# Combine cosine and LLM
def check_commodity_and_cosine(commodity):
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
        df = pd.read_excel(file_path)
        df = df[['Serial', 'COMMODITY']].copy()
        df[['Cosine_Similarity', 'LLM_Result']] = df['COMMODITY'].apply(lambda x: pd.Series(check_commodity_and_cosine(x)))
        df['Final_Match'] = df.apply(lambda row: 'Y' if row['Cosine_Similarity'] == 'Y' or row['LLM_Result'] == 'Y' else 'N', axis=1)
        output_file = "processed_file_with_final_match.xlsx"
        df.to_excel(output_file, index=False)
        return output_file
    except Exception as e:
        print(f"Error processing file: {e}")
        raise

@app.route('/')
def home():
    return render_template('index.html', references=REFERENCE_COMMODITY_LIST)

@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        file = request.files['file']
        if not file or file.filename == '':
            return "No file uploaded", 400

        upload_dir = 'uploads'
        os.makedirs(upload_dir, exist_ok=True)
        file_path = os.path.join(upload_dir, file.filename)
        file.save(file_path)

        processed_path = process_file(file_path)
        return send_file(processed_path, as_attachment=True)
    except Exception as e:
        return str(e), 500

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
    app.run(debug=True) 