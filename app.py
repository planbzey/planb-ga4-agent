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
st.set_page_config(page_title="PlanB Whisperer", page_icon="💬", layout="wide")

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

# --- MANUEL AI İSTEĞİ (gemini-pro + TEMPERATURE 0) ---
def ask_gemini_raw(prompt_text, temperature=0.0):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    data = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        # BURASI ÇOK ÖNEMLİ: Temperature 0 yaptık, yaratıcılık yok, sadece itaat var.
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 800
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
            return f"API Error: {response.text}"
    except Exception as e:
        return f"Request Failed: {e}"

def get_gemini_json(prompt):
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    
    # --- PROMPT GÜNCELLENDİ: ÖRNEKLİ ANLATIM (Few-Shot) ---
    sys_prompt = f"""You are a strict JSON generator for GA4 API. 
    Current Date: {today_str}.
    
    Goal: Convert user query to JSON.
    
    STRICT RULES:
    1. Output ONLY valid JSON. No text, no markdown.
    2. KEY REQUIREMENT: You MUST include "date_ranges", "metrics", "dimensions".
    3. FUTURE DATES: If user asks for "2 Dec 2025", YOU MUST USE "2025-12-02". Do not use 'today'.
    
    EXAMPLES:
    User: "dünkü ciro"
    JSON: {{"date_ranges": [{{"start_date": "yesterday", "end_date": "yesterday"}}], "dimensions": [], "metrics": [{{"name": "totalRevenue"}}]}}
    
    User: "revenue for december 2nd 2025"
    JSON: {{"date_ranges": [{{"start_date": "2025-12-02", "end_date": "2025-12-02"}}], "dimensions": [], "metrics": [{{"name": "totalRevenue"}}]}}
    
    User: "kasım 2025 en çok satan ürünler"
    JSON: {{"date_ranges": [{{"start_date": "2025-11-01", "end_date": "2025-11-30"}}], "dimensions": [{{"name": "itemName"}}], "metrics": [{{"name": "itemsPurchased"}}]}}
    """
    
    full_prompt = f"{sys_prompt}\nUser: {prompt}\nJSON:"
    
    # Temperature 0 ile çağırıyoruz
    raw_text = ask_gemini_raw(full_prompt, temperature=0.0)
    
    try:
        match = re.search(r"\{[\s\S]*\}", raw_text)
        if match:
            clean_json = match.group(0)
            parsed = json.loads(clean_json)
            
            # Son kontrol: Date range yoksa biz ekleyelim (Emniyet sübabı)
            if "date_ranges" not in parsed:
                 # Yapay zeka yine de unuttuysa, prompttan tarihi ayıklamayı deneyebiliriz ama şimdilik today verelim
                 # Ancak temperature 0 ile unutma ihtimali çok düşüktür.
                 parsed["date_ranges"] = [{"start_date": "today", "end_date": "today"}]
                 
            return parsed, raw_text
        return None, raw_text
    except Exception as e:
        return None, f"Hata: {raw_text}"

def get_gemini_summary(df, prompt):
    data_sample = df.head(10).to_string()
    full_prompt = f"Soru: '{prompt}'. Veri:\n{data_sample}\n\nKısa özet yaz."
    return ask_gemini_raw(full_prompt, temperature=0.7) # Yorum yaparken biraz yaratıcı olabilir

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
        for i, dim in enumerate(dimensions): 
            item[dim['name']] = row.dimension_values[i].value
        for i, met in enumerate(metrics): 
            item[met['name']] = row.metric_values[i].value
        data.append(item)
    return pd.DataFrame(data)

def export_to_sheet(df, prompt):
    gc = gspread.authorize(creds)
    sh = gc.create(f"Rapor: {prompt[:20]}")
    sh.sheet1.update_cell(1, 1, f"Soru: {prompt}")
    sh.sheet1.update([df.columns.values.tolist()] + df.values.tolist(), 'A3')
    sh.share(None, perm_type='anyone', role='reader')
    return sh.url

# --- ARAYÜZ ---
with st.sidebar:
    try: st.image("logo.png", use_container_width=True) 
    except: st.warning("Logo yok")
    st.markdown("---")
    
    df_brands = get_ga4_properties()
    selected_brand_data = None
    
    if not df_brands.empty:
        brand_list = sorted(df_brands['Marka Adi'].tolist())
        selected_brand = st.selectbox("Marka Seç:", brand_list)
        selected_brand_data = df_brands[df_brands['Marka Adi'] == selected_brand].iloc[0]
        st.success(f"✅ {selected_brand}")
        st.markdown("---")
        
        if st.button("🧹 SİSTEMİ SIFIRLA"):
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
                        else:
                            st.warning("Bu tarih için veri '0' döndü.")
                    except Exception as e:
                        st.error(f"GA4 Hatası: {e}")
                        with st.expander("Sorgulanan Tarih (JSON)"):
                            st.json(query_json) 
                else:
                    st.error("⚠️ AI JSON Üretemedi. (Debug Kutusuna Bakın)")
                    with st.expander("Debug Bilgisi"):
                        st.text(raw_response)

if st.session_state.last_data is not None:
    if st.button("📂 Sheets'e Aktar"):
        with st.spinner("Aktarılıyor..."):
            url = export_to_sheet(st.session_state.last_data, prompt)
            st.success("Bitti!")
            st.markdown(f"[👉 Aç]({url})")
