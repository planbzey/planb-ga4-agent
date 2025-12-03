import streamlit as st
import pandas as pd
import json
import gspread
import re
import datetime 
from google.oauth2 import service_account
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest
from google.analytics.admin import AnalyticsAdminServiceClient
import google.generativeai as genai
import time

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="PlanB Whisperer", page_icon="💬", layout="wide")

# --- CSS ---
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
    genai.configure(api_key=GEMINI_API_KEY)
    
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/analytics.readonly", 
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/analytics.edit"]
    )
except Exception as e:
    st.error(f"Ayar Hatası: {e}")
    st.stop()

# --- HAFIZA ---
if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_data" not in st.session_state:
    st.session_state.last_data = None
if "last_prompt" not in st.session_state:
    st.session_state.last_prompt = ""

# --- FONKSİYONLAR ---
@st.cache_data(ttl=300)
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

# --- GÜVENLİK AYARLARI ---
safety_config = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

def get_gemini_json(prompt):
    model = genai.GenerativeModel('gemini-1.5-flash', safety_settings=safety_config)
    
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    
    # --- PROMPT GÜNCELLENDİ: TEK GÜN MANTIĞI EKLENDİ ---
    sys_prompt = f"""You are a GA4 API expert.
    TODAY'S DATE: {today_str}.
    
    Your Goal: Convert user question to JSON.
    
    CRITICAL DATE RULES:
    1. If user asks for a specific SINGLE DAY (e.g. "2 Aralık", "December 2nd"), set 'start_date' and 'end_date' to the SAME date (e.g., "2025-12-02").
    2. If user asks for a MONTH (e.g. "Kasım 2025"), use full month range (e.g. "2025-11-01" to "2025-11-30").
    3. Turkish Months Mapping: Ocak=01, Şubat=02, Mart=03, Nisan=04, Mayıs=05, Haziran=06, Temmuz=07, Ağustos=08, Eylül=09, Ekim=10, Kasım=11, Aralık=12.
    
    Metrics Mapping:
    - Ciro, Gelir -> totalRevenue, purchaseRevenue
    - Satış Adedi -> itemsPurchased
    - Ziyaretçi -> activeUsers
    - Görüntülenme -> screenPageViews
    
    Output ONLY JSON.
    Example: {{"date_ranges": [{{"start_date": "2025-12-02", "end_date": "2025-12-02"}}], "dimensions": [{{"name": "itemName"}}], "metrics": [{{"name": "itemsPurchased"}}]}}
    """
    
    try:
        res = model.generate_content(f"{sys_prompt}\nUser Question: {prompt}")
        raw_text = res.text
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if match:
            return json.loads(match.group(0)), raw_text
        else:
            return None, raw_text
    except Exception as e:
        return None, str(e)

def get_gemini_summary(df, prompt):
    model = genai.GenerativeModel('gemini-1.5-flash', safety_settings=safety_config)
    data_sample = df.head(10).to_string()
    sys_prompt = f"Soru: '{prompt}'. Veri:\n{data_sample}\n\nÖzetle. Finansal yorum yap."
    try:
        res = model.generate_content(sys_prompt)
        return res.text
    except:
        return "⚠️ Veri çekildi."

def run_ga4_report(prop_id, query):
    client = BetaAnalyticsDataClient(credentials=creds)
    req = RunReportRequest(
        property=f"properties/{prop_id}",
        dimensions=[{"name": d['name']} for d in query.get('dimensions', [])],
        metrics=[{"name": m['name']} for m in query.get('metrics', [])],
        date_ranges=[query['date_ranges'][0]],
        limit=query.get('limit', 100)
    )
    res = client.run_report(req)
    data = []
    for row in res.rows:
        item = {}
        for i, dim in enumerate(query.get('dimensions', [])): item[dim['name']] = row.dimension_values[i].value
        for i, met in enumerate(query.get('metrics', [])): item[met['name']] = row.metric_values[i].value
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
        if st.button("🗑️ Temizle"):
            st.session_state.messages = []
            st.rerun()
    else:
        st.error("Marka yok. Robotu GA4'e ekle.")

st.subheader("PlanB GA4 Whisperer")

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("Bir soru sor..."):
    if selected_brand_data is None:
        st.error("Marka seçin.")
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
                            st.session_state.last_prompt = prompt
                        else:
                            st.warning("Veri '0' döndü.")
                    except Exception as e:
                        st.error(f"GA4 Hatası: {e}")
                else:
                    st.error("⚠️ AI Cevabı Anlaşılamadı.")
                    with st.expander("🕵️‍♂️ Debug"):
                        st.code(raw_response, language="text")

if st.session_state.last_data is not None:
    if st.button("📂 Sheets'e Aktar"):
        with st.spinner("Aktarılıyor..."):
            url = export_to_sheet(st.session_state.last_data, st.session_state.last_prompt)
            st.success("Bitti!")
            st.markdown(f"[👉 Aç]({url})")
