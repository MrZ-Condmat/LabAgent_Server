from openai import OpenAI
from dotenv import load_dotenv
import json
import os
import time

load_dotenv()

with open("lab_agent/config/models.json", "r", encoding="utf-8") as f:
    model_config = json.load(f).get("chatModel", {})

api_key = os.getenv("DEEPSEEK_CHAT_API_KEY")
model_name = model_config.get("name", "DeepSeek-V4-Pro")

print("Step 1: API key loaded:", bool(api_key))
print("Step 2: Creating client...")

client = OpenAI(
    api_key=api_key,
    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://llmapi.paratera.com"),
    timeout=30.0,
    max_retries=0,
)

print("Step 3: Sending request with model:", model_name)
t0 = time.time()

try:
    resp = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Reply with exactly: API OK"},
        ],
        temperature=0.1,
        max_tokens=20,
    )

    print("Step 4: Response received.")
    print("Elapsed:", round(time.time() - t0, 2), "seconds")
    print(resp.choices[0].message.content)

except Exception as e:
    print("API request failed.")
    print("Elapsed:", round(time.time() - t0, 2), "seconds")
    print("Error type:", type(e).__name__)
    print("Error:", e)
