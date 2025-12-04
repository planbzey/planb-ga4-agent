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

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="PlanB Whisperer", page_icon="⚡", layout="wide")

# --- CSS İLE MODERN MAKYAJ ---
st.markdown("""
<style>
    /* Ana Arka Plan ve Yazı Tipleri */
    .stApp {
        background-color: #f8f9fa;
    }
    
    /* Sohbet Baloncukları */
    .stChatMessage {
        background-color: #ffffff !important;
        border-radius: 20px;
        padding: 15px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        border: 1px solid #eee;
        margin-bottom: 15px;
    }
    
    /* Kullanıcı Baloncuğu */
    [data-testid="stChatMessage"][data-testid="user"] {
        background-color: #e3f2fd !important;
        border-bottom-right-radius: 5px;
    }
    
    /* Asistan Baloncuğu */
    [data-testid="stChatMessage"][data-testid="assistant"] {
        border-bottom-left-radius: 5px;
    }

    /* Sidebar Tasarımı */
    [data-testid="stSidebar"] {
        background-color: #1e1e1e;
    }
    [data-testid="stSidebar"] *, [data-testid="stSidebar"] p {
        color: #e0e0e0 !important;
    }

    /* Butonlar (Hızlı Menü) */
    .stButton>button {
        border-radius: 20px;
        border: 1px solid #ddd;
        background-color: white;
        color: #333;
        font-weight: 500;
        width: 100%;
        transition: all 0.3s;
    }
    .stButton>button:hover {
        border-color: #ff4b4b;
        color: #ff4b4b;
        background-color: #fff0f0;
    }
    
    /* Tablo Görünümü */
    [data-testid="stDataFrame"] {
        border-radius: 10px;
        overflow: hidden;
        border: 1px solid #eee;
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
    st.error(f"⚠️ Sistem Ayarları Eksik: {e}")
    st.stop()

# --- HAFIZA ---
if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_data" not in st.session_state:
    st.session_state.last_data = None
if "active_model_name" not in st.session_state:
    st.session_state.active_model_name = "gemini-1.5-flash"
if "last_prompt" not in st.session_state:
    st.session_state.last_prompt = "Rapor"

# --- GRAFİK MOTORU (YENİ) ---
def auto_visualize(df):
    """Veriye bakıp otomatik grafik seçer"""
    columns = [c.lower() for c in df.columns]
    
    # Eğer tarih varsa Çizgi Grafik çiz
    if any(x in columns for x in ['date', 'tarih', 'yearmonth', 'gün', 'day']):
        # Tarih olmayan ilk sayısal sütunu bulmaya çalış
        numeric_cols = df.select_dtypes(include=['number']).columns
        if len(numeric_cols) > 0:
            st.caption("📈 Zaman Grafiği")
            # Streamlit otomatik tarih sütununu X ekseni yapar
            st.line_chart(df, y=numeric_cols)
            return

    # Eğer sadece kategorik veri varsa Bar Grafik çiz
    if len(df) > 1 and len(df) < 20: # Çok kalabalıksa çizme
        st.caption("📊 Karşılaştırma Grafiği")
        st.bar_chart(df)

# --- GEMINI & GA4 FONKSİYONLARI ---
def ask_gemini_raw(prompt_text, temperature=0.0):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    data = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 2000}
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        else:
            return f"Hata: {response.text}"
    except Exception as e:
        return f"Request Failed: {e}"

def get_gemini_json(prompt):
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    sys_prompt = f"""You are a GA4 API expert. TODAY: {today_str}.
    Task: Convert user question to JSON for GA4 report.
    RULES:
    1. Output ONLY valid JSON. No markdown.
    2. Suggest metrics like: totalRevenue, purchaseRevenue, activeUsers, sessions, itemsPurchased, bounceRate.
    Example: {{"date
