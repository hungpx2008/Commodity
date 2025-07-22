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
import traceback
import sys
import io

# Set stdout and stderr to utf-8 for Windows
if sys.platform == "win32":
    if sys.stdout.encoding != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    if sys.stderr.encoding != 'utf-8':
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

app = Flask(__name__)

# Global variables to store the current processing task and cancellation flag
current_task = None
upload_cancelled = False
openai_api_error = False

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
        with open(REFERENCE_PATH, "r", encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print("Reference file not found, using default reference.")
        return [""]
    except json.JSONDecodeError as e:
        print(f"Invalid JSON format in reference file: {e}")
        return [""]
    except Exception as e:
        print(f"Could not load reference list: {e}")
        return [""]

# Save references to file
def save_reference_list(reference_list):
    try:
        os.makedirs(os.path.dirname(REFERENCE_PATH), exist_ok=True)
        with open(REFERENCE_PATH, "w", encoding='utf-8') as f:
            json.dump(reference_list, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Could not save reference list: {e}")
        raise

# Load global reference list
REFERENCE_COMMODITY_LIST = load_reference_list()

# Cosine similarity check
def check_cosine_similarity(commodity):
    try:
        if not commodity or pd.isna(commodity) or str(commodity).strip() == '':
            return 'N'
        
        commodity_str = str(commodity).strip()
        for ref in REFERENCE_COMMODITY_LIST:
            if not ref or str(ref).strip() == '':
                continue
            vectorizer = CountVectorizer().fit_transform([commodity_str, str(ref)])
            cosine_sim = cosine_similarity(vectorizer[0:1], vectorizer[1:2])
            if cosine_sim[0][0] > 0.1:
                return 'Y'
        return 'N'
    except Exception as e:
        print(f"Error in cosine similarity check: {e}")
        return 'N'

class RateLimitError(Exception):
    pass

# LLM prompt using OR logic
def check_commodity_from_llm(commodity):
    global openai_api_error
    try:
        if not commodity or pd.isna(commodity) or str(commodity).strip() == '':
            return 'N'
            
        reference_string = ', or '.join(f"'{ref}'" for ref in REFERENCE_COMMODITY_LIST)
        prompt = (f"You are an expert in commodities. Your task is to determine if a commodity matches any of the following references: "
            f"Does the phrase '{commodity}' mean the same as the following: "
            f"{reference_string}? Only reply 'Y' for yes or 'N' for no."
        )
        
        completion = client.chat.completions.create(
            model="deepseek/deepseek-r1-0528:free",
            messages=[{"role": "user", "content": prompt}],
            timeout=30  # Add timeout
        )
        result = completion.choices[0].message.content.strip()
        return 'Y' if result == 'Y' else 'N'
    except Exception as e:
        openai_api_error = True  # Set flag on any API error
        error_msg = str(e).lower()
        if 'rate limit' in error_msg or '429' in error_msg:
            print(f"API Rate limit exceeded, switching to cosine similarity only. Error: {e}")
        elif 'timeout' in error_msg:
            print(f"API Timeout error, switching to cosine similarity only. Error: {e}")
        elif 'connection' in error_msg:
            print(f"API Connection error, switching to cosine similarity only. Error: {e}")
        else:
            print(f"An unexpected error occurred with the LLM, switching to cosine similarity only. Error: {e}")
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
                return future_cosine.result(timeout=60), future_llm.result(timeout=60)
            except concurrent.futures.TimeoutError:
                print("Timeout occurred during processing")
                return future_cosine.result(), 'N'
            except Exception as e:
                print(f"Error in concurrent execution: {e}")
                return future_cosine.result(), 'N'

# Process uploaded Excel
def process_file(file_path):
    try:
        # Kiểm tra file có tồn tại không
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File không tồn tại: {file_path}")
        
        # Kiểm tra kích thước file
        file_size = os.path.getsize(file_path)
        if file_size == 0:
            raise ValueError("File rỗng, vui lòng chọn file có dữ liệu")
        
        # Kiểm tra định dạng file
        if not file_path.lower().endswith(('.xlsx', '.xls')):
            raise ValueError("Chỉ hỗ trợ file Excel (.xlsx, .xls)")
        
        # Đọc toàn bộ file Excel
        try:
            df = pd.read_excel(file_path)
        except pd.errors.EmptyDataError:
            raise ValueError("File Excel không có dữ liệu")
        except pd.errors.ParserError as e:
            raise ValueError(f"File Excel bị lỗi định dạng: {str(e)}")
        except Exception as e:
            if "No such file or directory" in str(e):
                raise FileNotFoundError("Không thể đọc file Excel")
            else:
                raise ValueError(f"Lỗi đọc file Excel: {str(e)}")
        
        # Kiểm tra DataFrame có rỗng không
        if df.empty:
            raise ValueError("File Excel không có dữ liệu")
        
        # Tìm cột COMMODITY một cách linh hoạt
        commodity_column = None
        available_columns = list(df.columns)
        
        for col in df.columns:
            if str(col).upper().strip() == 'COMMODITY':
                commodity_column = col
                break
        
        if commodity_column is None:
            columns_list = ', '.join([f"'{col}'" for col in available_columns])
            raise ValueError(f"File Excel có vẻ không có cột 'COMMODITY' hoặc không đúng chính tả.")
        
        # Kiểm tra cột COMMODITY có dữ liệu không
        if df[commodity_column].isna().all():
            raise ValueError(f"Cột '{commodity_column}' không có dữ liệu")
        
        # Kiểm tra số lượng dòng
        if len(df) > 10000:
            raise ValueError(f"File quá lớn ({len(df)} dòng). Chỉ hỗ trợ tối đa 10,000 dòng")
        
        print(f"Đang xử lý {len(df)} dòng dữ liệu...")
        
        # Áp dụng logic xử lý cho cột COMMODITY
        commodity_results = df[commodity_column].apply(lambda x: pd.Series(check_commodity_and_cosine(x)))
        df['Cosine_Similarity'] = commodity_results[0]
        df['LLM_Result'] = commodity_results[1]
        
        # Tạo cột Final_Match dựa trên logic OR
        df['Final_Match'] = df.apply(
            lambda row: 'Y' if row['Cosine_Similarity'] == 'Y' or row['LLM_Result'] == 'Y' else 'N', 
            axis=1
        )
        
        # Tạo thư mục output nếu chưa có
        output_dir = "output"
        os.makedirs(output_dir, exist_ok=True)
        
        # Lưu file với tên mới
        output_file = os.path.join(output_dir, "processed_file_with_final_match.xlsx")
        try:
            df.to_excel(output_file, index=False)
        except PermissionError:
            raise ValueError("Không thể ghi file kết quả. File có thể đang được mở bởi ứng dụng khác")
        except Exception as e:
            raise ValueError(f"Lỗi khi lưu file kết quả: {str(e)}")
        
        return output_file
        
    except Exception as e:
        print(f"Error processing file: {e}")
        print(f"Full traceback: {traceback.format_exc()}")
        raise

@app.route('/')
def home():
    return render_template('index.html', references=REFERENCE_COMMODITY_LIST)

# Simulate a long-running task (file processing)
def long_running_task(file):
    global current_task, upload_cancelled
    try:
        for i in range(10):
            if upload_cancelled:
                break
            time.sleep(1)
            print(f"Processing {file}... {i + 1}/10")
        
        if not upload_cancelled:
            print(f"File {file} processed successfully!")
    finally:
        current_task = None

# Upload route với xử lý lỗi chi tiết
@app.route('/upload', methods=['POST'])
def upload_file():
    global current_task, upload_cancelled, openai_api_error

    try:
        # Kiểm tra có file được upload không
        if 'file' not in request.files:
            return jsonify({
                "error": "Không có file nào được chọn",
                "error_type": "NO_FILE"
            }), 400

        file = request.files['file']
        if not file or file.filename == '':
            return jsonify({
                "error": "Vui lòng chọn một file để upload",
                "error_type": "EMPTY_FILE"
            }), 400

        # Kiểm tra định dạng file
        allowed_extensions = {'.xlsx', '.xls'}
        file_ext = os.path.splitext(file.filename.lower())[1]
        if file_ext not in allowed_extensions:
            return jsonify({
                "error": f"Định dạng file không được hỗ trợ. Chỉ chấp nhận file Excel (.xlsx, .xls). File của bạn: {file_ext}",
                "error_type": "INVALID_FORMAT"
            }), 400

        # Tạo thư mục upload
        upload_dir = 'uploads'
        try:
            os.makedirs(upload_dir, exist_ok=True)
        except Exception as e:
            return jsonify({
                "error": f"Không thể tạo thư mục upload: {str(e)}",
                "error_type": "UPLOAD_DIR_ERROR"
            }), 500

        file_path = os.path.join(upload_dir, file.filename)
        
        # Lưu file
        try:
            file.save(file_path)
        except Exception as e:
            return jsonify({
                "error": f"Không thể lưu file: {str(e)}",
                "error_type": "SAVE_ERROR"
            }), 500

        # Reset error flags
        openai_api_error = False

        # Start long-running task
        upload_cancelled = False
        current_task = threading.Thread(target=long_running_task, args=(file.filename,))
        current_task.start()

        # Process file
        try:
            processed_path = process_file(file_path)
        except FileNotFoundError as e:
            return jsonify({
                "error": str(e),
                "error_type": "FILE_NOT_FOUND"
            }), 404
        except ValueError as e:
            return jsonify({
                "error": str(e),
                "error_type": "VALIDATION_ERROR"
            }), 400
        except pd.errors.EmptyDataError:
            return jsonify({
                "error": "File Excel rỗng hoặc không có dữ liệu",
                "error_type": "EMPTY_DATA"
            }), 400
        except pd.errors.ParserError:
            return jsonify({
                "error": "File Excel bị lỗi định dạng, không thể đọc được",
                "error_type": "PARSE_ERROR"
            }), 400
        except PermissionError:
            return jsonify({
                "error": "Không có quyền truy cập file hoặc file đang được sử dụng",
                "error_type": "PERMISSION_ERROR"
            }), 403
        except MemoryError:
            return jsonify({
                "error": "File quá lớn, không đủ bộ nhớ để xử lý",
                "error_type": "MEMORY_ERROR"
            }), 413
        except Exception as e:
            return jsonify({
                "error": f"Lỗi không xác định khi xử lý file: {str(e)}",
                "error_type": "PROCESSING_ERROR",
                "details": str(e)
            }), 500

        # Kiểm tra file output có tồn tại không
        if not os.path.exists(processed_path):
            return jsonify({
                "error": "File kết quả không được tạo ra",
                "error_type": "OUTPUT_ERROR"
            }), 500

        # Notify user if OpenAI API was not used
        response = send_file(
            processed_path, 
            as_attachment=True, 
            download_name="processed_file.xlsx"
        )
        if openai_api_error:
            response.headers['X-API-Fallback'] = 'true'
        return response

    except Exception as e:
        # Log full error for debugging
        print(f"Unexpected error in upload_file: {e}")
        print(f"Full traceback: {traceback.format_exc()}")
        
        return jsonify({
            "error": f"Lỗi hệ thống không xác định: {str(e)}",
            "error_type": "SYSTEM_ERROR"
        }), 500

# Cancel upload route
@app.route('/cancel-upload', methods=['POST'])
def cancel_upload():
    global current_task, upload_cancelled

    try:
        if current_task is not None:
            upload_cancelled = True
            current_task.join(timeout=5)  # Wait max 5 seconds
            return jsonify({"message": "Quá trình xử lý đã được hủy bỏ."}), 200
        else:
            return jsonify({"error": "Không có quá trình nào đang chạy để hủy bỏ."}), 400
    except Exception as e:
        return jsonify({
            "error": f"Lỗi khi hủy bỏ quá trình: {str(e)}",
            "error_type": "CANCEL_ERROR"
        }), 500

# Status route
@app.route('/status', methods=['GET'])
def status():
    try:
        if current_task is not None and current_task.is_alive():
            return jsonify({"status": "Đang xử lý..."}), 200
        else:
            return jsonify({"status": "Không có quá trình nào đang chạy"}), 200
    except Exception as e:
        return jsonify({
            "error": f"Lỗi khi kiểm tra trạng thái: {str(e)}",
            "error_type": "STATUS_ERROR"
        }), 500

# Set reference route
@app.route('/set-reference', methods=['POST'])
def set_reference():
    global REFERENCE_COMMODITY_LIST
    try:
        reference_raw = request.form.get('reference') or request.json.get('reference')
        if not reference_raw:
            return jsonify({
                "error": "Thiếu dữ liệu reference",
                "error_type": "MISSING_REFERENCE"
            }), 400

        # Validate reference data
        reference_list = [r.strip() for r in reference_raw.split(',') if r.strip()]
        if not reference_list:
            return jsonify({
                "error": "Reference không được để trống",
                "error_type": "EMPTY_REFERENCE"
            }), 400

        if len(reference_list) > 50:
            return jsonify({
                "error": "Quá nhiều reference (tối đa 50)",
                "error_type": "TOO_MANY_REFERENCES"
            }), 400

        REFERENCE_COMMODITY_LIST = reference_list
        save_reference_list(REFERENCE_COMMODITY_LIST)
        return render_template('index.html', references=REFERENCE_COMMODITY_LIST)
    
    except Exception as e:
        return jsonify({
            "error": f"Lỗi khi cập nhật reference: {str(e)}",
            "error_type": "REFERENCE_UPDATE_ERROR"
        }), 500

# Global error handler
@app.errorhandler(404)
def not_found_error(error):
    return jsonify({
        "error": "Không tìm thấy trang yêu cầu",
        "error_type": "NOT_FOUND"
    }), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        "error": "Lỗi hệ thống nội bộ",
        "error_type": "INTERNAL_ERROR"
    }), 500

if __name__ == "__main__":
    # Ensure directories exist
    for directory in ['uploads', 'output', 'config']:
        if not os.path.exists(directory):
            os.makedirs(directory)
    
    app.run(host='0.0.0.0', port=8000, debug=True)