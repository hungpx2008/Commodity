import pandas as pd
from openai import OpenAI
import time
# Configure OpenRouter API
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key="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",  # Thay bằng API key của bạn
)
# Read Excel file
input_file = '/content/1.xlsx'
df = pd.read_excel(input_file)
df = df[['Serial', 'COMMODITY']].copy()
def check_commodity(commodity):
    prompt = (
        f"Does the phrase '{commodity}' mean 'FLUID SEAMLESS STEEL' or something similar in meaning? "
        "Return only 'Y' if it matches or is similar, 'N' if it does not."
    )
    try:
        completion = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            extra_headers={
                "HTTP-Referer": "YOUR_SITE_URL",  # Thay bằng URL của bạn (nếu có)
                "X-Title": "YOUR_SITE_NAME"  # Thay bằng tên site của bạn (nếu có)
            },
            extra_body={}
        )
        result = completion.choices[0].message.content.strip()
        if result in ['Y', 'N']:
            return result
        else:
            print(f"Unexpected response for '{commodity}': {result}")
            return 'N'
    except Exception as e:
        print(f"Error processing '{commodity}': {e}")
        return 'N'

# Apply the function to the COMMODITY column with a delay to avoid rate limits
df['Matches_FLUID_SEAMLESS_STEEL'] = df['COMMODITY'].apply(lambda x: check_commodity(x) or time.sleep(0.5))

# Print the results
print(df)
# Ghi kết quả vào file mới
output_file = "/content/sample_data/Result_with_Confirm.xlsx"
df.to_excel(output_file, index=False)

print(f"✅ Done. File saved to: {output_file}")