import streamlit as st
import pandas as pd
import json
import gspread
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

# --- CSS TASARIM ---
st.markdown("""
<style>
    .stChatMessage {
        background-color: #ffffff !important;
        border-radius: 15px;
        padding: 15px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        margin-bottom: 10px;
        border: 1px solid #e0e0e0;
    }
    [data-testid="stChatMessage"] * {
        color: #000000 !important;
    }
    .stChatMessage .stAvatar {
        background-color: #ff4b4b !important;
        color: white !important;
    }
    [data-testid="stSidebar"] {
        background-color: #000000;
    }
    [data-testid="stSidebar"] *, [data-testid="stSidebar"] p {
        color: #ffffff !important;
    }
    .stButton>button {
        background-color: #ff4b4b;
        color: white !important;
        border: none;
        font-weight: bold;
    }
    /* Debug Kutusu Okunabilirliği */
    .stCode {
        background-color: #f0f0f0 !important;
        color: #000000 !important;
    }
    .stAlert {
        background-color: #ffffff !important;
        color: #000000 !important;
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
                "https://www.googleapis.com/auth/spreadsheets",
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
# SON SORUYU DA HAFIZADA TUTALIM
if "last_prompt" not in st.session_state:
    st.session_state.last_prompt = "Rapor"

# --- FONKSİYONLAR ---
def get_ga4_properties():
    try:
        admin_client = AnalyticsAdminServiceClient(credentials=creds)
        results = []
        for account in admin_client.list_account_summaries():
            for property_summary in account.property_summaries:
                results.append({
                    "Marka Adi": property_summary.display_name,
                    "GA4_Property_ID": property_summary.property.split('/')[-1]
                })
        return pd.DataFrame(results)
    except Exception as e:
        return pd.DataFrame()

# --- 1. ADIM: ÇALIŞAN MODELİ BUL ---
def find_best_model():
    if st.session_state.active_model_name:
        return st.session_state.active_model_name, None
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    try:
        resp = requests.get(url)
        data = resp.json()
        
        if "error" in data:
            return None, f"API Key Hatası: {data['error']['message']}"
            
        if "models" in data:
            for m in data['models']:
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    if "gemini" in m["name"]:
                        found_name = m["name"].replace("models/", "")
                        st.session_state.active_model_name = found_name
                        return found_name, None
            
            first_model = data['models'][0]['name'].replace("models/", "")
            st.session_state.active_model_name = first_model
            return first_model, None
            
    except Exception as e:
        return None, str(e)
    
    return "gemini-1.5-flash", None

# --- 2. ADIM: DİNAMİK MODEL İLE İSTEK AT ---
def ask_gemini_raw(prompt_text, temperature=0.0):
    model_name, error = find_best_model()
    
    if error:
        return f"Model Error: {error}"
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
    
    headers = {'Content-Type': 'application/json'}
    data = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 2000
        },
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

def get_gemini_json(prompt):
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    
    sys_prompt = f"""You are a GA4 API expert. TODAY: {today_str}.
    Task: Convert user question to JSON.
    
    RULES:
    1. If user says specific date (e.g. "2 Dec 2025"), USE THAT EXACT DATE for both start_date and end_date.
    2. Output ONLY JSON.
    3. Metrics: totalRevenue, purchaseRevenue, activeUsers, sessions, itemsPurchased.
    
    Example: {{"date_ranges": [{{"start_date": "2025-12-02", "end_date": "2025-12-02"}}], "dimensions": [{{"name": "itemName"}}], "metrics": [{{"name": "itemsPurchased"}}]}}
    """
    
    full_prompt = f"{sys_prompt}\nReq: {prompt}"
    raw_text = ask_gemini_raw(full_prompt, temperature=0.0)
    
    try:
        match = re.search(r"\{[\s\S]*\}", raw_text)
        if match:
            clean_json = match.group(0)
            parsed = json.loads(clean_json)
            if "date_ranges" not in parsed:
                 parsed["date_ranges"] = [{"start_date": "today", "end_date": "today"}]
            return parsed, raw_text
        return None, raw_text
    except Exception as e:
        return None, raw_text

def get_gemini_summary(df, prompt):
    data_sample = df.head(10).to_string()
    full_prompt = f"Soru: '{prompt}'. Veri:\n{data_sample}\n\nKısa özet yaz."
    return ask_gemini_raw(full_prompt, temperature=0.5)

def run_ga4_report(prop_id, query):
    client = BetaAnalyticsDataClient(credentials=creds)
    dimensions = [{"name": d['name']} for d in query.get('dimensions', [])]
    metrics = [{"name": m['name']} for m in query.get('metrics', [])]
    date_ranges = [query['date_ranges'][0]]
    
    req = RunReportRequest(
        property=f"properties/{prop_id}",
        dimensions=dimensions,
        metrics=metrics,
        date_ranges=date_ranges,
        limit=query.get('limit', 100)
    )
    res = client.run_report(req)
    data = []
    for row in res.rows:
        item = {}
        for i, dim in enumerate(dimensions): item[dim['name']] = row.dimension_values[i].value
        for i, met in enumerate(metrics): item[met['name']] = row.metric_values[i].value
        data.append(item)
    return pd.DataFrame(data)

def export_to_sheet(df, prompt_text):
    gc = gspread.authorize(creds)
    # Hata önleyici: Eğer prompt boşsa veya None ise varsayılan başlık kullan
    safe_title = str(prompt_text)[:20] if prompt_text else "Rapor"
    
    sh = gc.create(f"Rapor: {safe_title}")
    sh.sheet1.update_cell(1, 1, f"Soru: {prompt_text}")
    sh.sheet1.update([df.columns.values.tolist()] + df.values.tolist(), 'A3')
    sh.share(None, perm_type='anyone', role='reader')
    return sh.url

# --- ARAYÜZ ---
with st.sidebar:
    try: st.image("logo.png", use_container_width=True) 
    except: st.warning("Logo yok")
    st.markdown("---")
    
    model_name, err = find_best_model()
    if err: st.error(err)
    else: st.success(f"🚀 {model_name}")

    df_brands = get_ga4_properties()
    selected_brand_data = None
    
    if not df_brands.empty:
        brand_list = sorted(df_brands['Marka Adi'].tolist())
        selected_brand = st.selectbox("Marka Seç:", brand_list)
        selected_brand_data = df_brands[df_brands['Marka Adi'] == selected_brand].iloc[0]
        st.success(f"✅ {selected_brand}")
        st.markdown("---")
        if st.button("🗑️ SİSTEMİ SIFIRLA"):
            st.session_state.clear()
            st.rerun()
    else:
        st.error("Marka bulunamadı. Robotu ekleyin.")

st.subheader("PlanB GA4 Whisperer")

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("Bir soru sor..."):
    if selected_brand_data is None:
        st.error("Lütfen marka seçin.")
    else:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Analiz..."):
                query_json, raw_response = get_gemini_json(prompt)
                
                if query_json:
                    try:
                        df = run_ga4_report(str(selected_brand_data['GA4_Property_ID']), query_json)
                        if not df.empty:
                            summary = get_gemini_summary(df, prompt)
                            st.markdown(summary)
                            st.dataframe(df, use_container_width=True, hide_index=True)
                            
                            st.session_state.messages.append({"role": "assistant", "content": summary})
                            st.session_state.last_data = df
                            # BURADA SON SORUYU HAFIZAYA ATIYORUZ
                            st.session_state.last_prompt = prompt
                        else:
                            st.warning("Bu tarih için veri '0' döndü.")
                    except Exception as e:
                        st.error(f"GA4 Hatası: {e}")
                        with st.expander("Teknik Detay"):
                             st.json(query_json)
                else:
                    st.error("⚠️ AI JSON Üretemedi.")
                    with st.expander("Debug"):
                        st.code(raw_response)

if st.session_state.last_data is not None:
    if st.button("📂 Sheets'e Aktar"):
        with st.spinner("Aktarılıyor..."):
            # DÜZELTİLDİ: prompt yerine session_state'deki last_prompt kullanılıyor
            url = export_to_sheet(st.session_state.last_data, st.session_state.last_prompt)
            st.success("Bitti!")
            st.markdown(f"[👉 Aç]({url})")
