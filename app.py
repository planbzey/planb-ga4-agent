import streamlit as st
import pandas as pd
import json
import re
import datetime 
import requests 
from google.oauth2 import service_account
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest
from google.analytics.admin import AnalyticsAdminServiceClient
import time

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="PlanB Whisperer", page_icon="⚡", layout="wide")

# --- CSS TASARIM (YENİ: TWITTER MAVİSİ & SİYAH) ---
st.markdown("""
<style>
    /* 1. GENEL ARKA PLAN (SİYAH) */
    .stApp {
        background-color: #000000;
        color: #ffffff;
    }

    /* 2. YAN MENÜ (TWITTER MAVİSİ) */
    [data-testid="stSidebar"] {
        background-color: #1DA1F2; /* Twitter Blue */
    }
    /* Yan Menü Yazı Rengi (Beyaz) */
    [data-testid="stSidebar"] *, [data-testid="stSidebar"] p, [data-testid="stSidebar"] label {
        color: #ffffff !important;
    }
    /* Selectbox vb. koyu görünsün */
    [data-testid="stSidebar"] .stSelectbox > div > div {
        color: #000000 !important;
    }
    
    /* 3. SOHBET BALONLARI (KOYU GRİ) */
    .stChatMessage {
        background-color: #1c1c1c !important; /* Koyu Gri */
        border-radius: 12px;
        padding: 20px;
        border: 1px solid #333333;
        margin-bottom: 15px;
    }
    /* Balon içi yazılar (Beyaz) */
    [data-testid="stChatMessage"] * {
        color: #ffffff !important;
    }
    /* Kullanıcı/Asistan İkonu */
    .stChatMessage .stAvatar {
        background-color: #1DA1F2 !important; /* Mavi İkon */
        color: white !important;
    }

    /* 4. TABLO DÜZENLEMELERİ */
    [data-testid="stDataFrame"] {
        background-color: #1c1c1c;
    }
    /* Tablo İkonları (Beyaz) */
    [data-testid="stDataFrame"] button {
        color: #ffffff !important; 
    }
    [data-testid="stDataFrame"] svg {
        fill: #ffffff !important;
    }

    /* 5. BUTONLAR */
    .stButton>button {
        background-color: #1DA1F2; /* Mavi Buton */
        color: white !important;
        border: none;
        font-weight: 600;
        border-radius: 8px;
        padding: 0.5rem 1rem;
    }
    .stButton>button:hover {
        background-color: #0d8bd9; /* Hoverda koyu mavi */
    }

    /* 6. DEBUG VE HATA KUTULARI */
    .stCode, .stAlert {
        background-color: #222222 !important;
        border: 1px solid #444444;
        color: #ffffff !important;
    }
    
    /* Başlıklar */
    h1, h2, h3 {
        color: #ffffff !important;
    }
</style>
""", unsafe_allow_html=True)

# --- AYARLAR ---
try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/analytics.readonly", 
                "https://www.googleapis.com/auth/analytics.edit"]
    )
except Exception as e:
    st.error(f"Sistem Ayarları Eksik: {e}")
    st.stop()

# --- HAFIZA ---
if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_data" not in st.session_state:
    st.session_state.last_data = None
if "active_model_name" not in st.session_state:
    st.session_state.active_model_name = None

# --- FONKSİYONLAR ---

# 1. HİYERARŞİ (HESAP > MÜLK)
def get_ga4_hierarchy():
    try:
        admin_client = AnalyticsAdminServiceClient(credentials=creds)
        results = []
        for account_summary in admin_client.list_account_summaries():
            account_name = account_summary.display_name
            for prop in account_summary.property_summaries:
                results.append({
                    "Account_Name": account_name,
                    "Property_Name": prop.display_name,
                    "GA4_Property_ID": prop.property.split('/')[-1]
                })
        return pd.DataFrame(results)
    except Exception as e:
        return pd.DataFrame()

# 2. MODEL SEÇİCİ
def find_best_model():
    if st.session_state.active_model_name:
        return st.session_state.active_model_name, None
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    try:
        resp = requests.get(url)
        data = resp.json()
        if "models" in data:
            for m in data['models']:
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    if "gemini" in m["name"]:
                        found_name = m["name"].replace("models/", "")
                        st.session_state.active_model_name = found_name
                        return found_name, None
            first_model = data['models'][0]['name'].replace("models/", "")
            return first_model, None
    except Exception as e:
        return None, str(e)
    return "gemini-1.5-flash", None

# 3. AI İSTEĞİ
def ask_gemini_raw(prompt_text, temperature=0.0):
    model_name, error = find_best_model()
    if error: return f"Model Error: {error}"
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    data = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 2000},
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        else:
            return f"API ERROR ({model_name}): {response.text}"
    except Exception as e:
        return f"Request Failed: {e}"

# 4. JSON DÖNÜŞTÜRÜCÜ
def get_gemini_json(prompt):
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    
    sys_prompt = f"""You are a GA4 API expert. TODAY: {today_str}.
    Task: Convert user question to JSON.
    
    DATE LOGIC:
    1. "son 30 gün", "last 30 days" -> start_date: "30daysAgo", end_date: "yesterday"
    2. "geçen ay", "last month" -> CALCULATE previous month dates based on TODAY.
    3. "bu yıl", "this year" -> start_date: "yearToDate", end_date: "yesterday"
    4. "dün", "yesterday" -> start_date: "yesterday", end_date: "yesterday"
    5. Exact date (e.g. "2 Dec") -> Use YYYY-MM-DD.
    
    Metrics Mapping:
    - Ciro/Revenue -> purchaseRevenue
    - Satış/Sales -> itemsPurchased
    - Kullanıcı/Users -> activeUsers
    - Oturum/Sessions -> sessions
    
    Output ONLY JSON.
    Example: {{"date_ranges": [{{"start_date": "30daysAgo", "end_date": "yesterday"}}], "dimensions": [{{"name": "sessionSource"}}], "metrics": [{{"name": "purchaseRevenue"}}]}}
    """
    
    full_prompt = f"{sys_prompt}\nReq: {prompt}"
    raw_text = ask_gemini_raw(full_prompt, temperature=0.0)
    
    try:
        match = re.search(r"\{[\s\S]*\}", raw_text)
        if match:
            clean_json = match.group(0)
            parsed = json.loads(clean_json)
            if "date_ranges" not in parsed:
                 parsed["date_ranges"] = [{"start_date": "28daysAgo", "end_date": "yesterday"}]
            return parsed, raw_text
        return None, raw_text
    except Exception as e:
        return None, raw_text

def get_gemini_summary(df, prompt):
    data_sample = df.head(10).to_string()
    full_prompt = f"Soru: '{prompt}'. Veri:\n{data_sample}\n\nBu veriye bakarak 1-2 cümlelik kısa özet yap. Rakamları yuvarla (Örn: 1.2M TL)."
    return ask_gemini_raw(full_prompt, temperature=0.5)

def run_ga4_report(prop_id, query):
    client = BetaAnalyticsData
