
import pandas as pd
from openai import OpenAI
import time
import io
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse

# Initialize FastAPI app
app = FastAPI(title="Excel Processing API")

# Add CORS middleware to allow the frontend to communicate with this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins for simplicity
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods (GET, POST, etc.)
    allow_headers=["*"],  # Allows all headers
)

# Configure OpenRouter API client
# WARNING: It is not secure to hardcode API keys in the source code.
# It is recommended to use environment variables.
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key="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",
)

def check_commodity(commodity: str):
    """
    Calls the AI model to check if the commodity matches a specific phrase.
    """
    if not commodity or pd.isna(commodity):
        return 'N'  # Return 'N' for empty or NaN values

    prompt = (
        f"Does the phrase '{commodity}' mean 'FLUID SEAMLESS STEEL' or something similar in meaning? "
        "Return only 'Y' if it matches or is similar, 'N' if it does not."
    )
    try:
        completion = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            extra_headers={
                "HTTP-Referer": "http://localhost", # Example Referer
                "X-Title": "Excel Processor" # Example Title
            },
        )
        result = completion.choices[0].message.content.strip()
        # Ensure the result is either 'Y' or 'N'
        return result if result in ['Y', 'N'] else 'N'
    except Exception as e:
        print(f"Error processing '{commodity}': {e}")
        return 'N'

@app.post("/process-excel/")
async def process_excel_file(file: UploadFile = File(...)):
    """
    API endpoint to upload an Excel file, process it, and return the result.
    """
    # Read the uploaded excel file from memory into a pandas DataFrame
    contents = await file.read()
    input_df = pd.read_excel(io.BytesIO(contents))

    # Ensure required columns exist, using a copy to avoid SettingWithCopyWarning
    df = input_df[['Serial', 'COMMODITY']].copy()

    # Apply the AI check to the 'COMMODITY' column
    # A delay is added between each call to avoid overwhelming the API
    results = []
    for commodity in df['COMMODITY']:
        results.append(check_commodity(commodity))
        time.sleep(0.5)  # 0.5-second delay

    df['Matches_FLUID_SEAMLESS_STEEL'] = results

    # Save the processed DataFrame to an in-memory Excel file (BytesIO buffer)
    output_buffer = io.BytesIO()
    df.to_excel(output_buffer, index=False)
    output_buffer.seek(0)  # Rewind the buffer to the beginning

    # Return the Excel file in the response so the user can download it
    return StreamingResponse(
        output_buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=Result_with_Confirm.xlsx"}
    )

@app.get("/")
def read_root():
    """
    Root endpoint to check if the API is running.
    """
    return {"message": "Welcome to the Excel Processing API. Use the /process-excel/ endpoint to upload a file."}
