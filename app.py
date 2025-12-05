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
    .stApp { background-color: #f8f9fa; }
    .stChatMessage {
        background-color: #ffffff !important;
        border-radius: 20px;
        padding: 15px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        border: 1px solid #eee;
        margin-bottom: 15px;
    }
    [data-testid="stChatMessage"][data-testid="user"] {
        background-color: #e3f2fd !important;
        border-bottom-right-radius: 5px;
    }
    [data-testid="stChatMessage"][data-testid="assistant"] {
        border-bottom-left-radius: 5px;
    }
    [data-testid="stSidebar"] { background-color: #1e1e1e; }
    [data-testid="stSidebar"] *, [data-testid="stSidebar"] p { color: #e0e0e0 !important; }
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

# --- GRAFİK MOTORU ---
def auto_visualize(df):
    columns = [c.lower() for c in df.columns]
    
    # Eğer Cihaz, Şehir gibi kategorik veriler varsa Pasta Grafiği çizelim
    categorical_cols = df.select_dtypes(include=['object']).columns
    if len(categorical_cols) > 0 and 'date' not in columns and 'tarih' not in columns:
         # İlk kategorik sütunu al (örn: deviceCategory)
         cat_col = categorical_cols[0]
         numeric_cols = df.select_dtypes(include=['number']).columns
         if len(numeric_cols) > 0:
             st.caption(f"📊 {cat_col} Dağılımı")
             st.bar_chart(df, x=cat_col, y=numeric_cols[0])
             return

    # Zaman grafiği kontrolü
    if any(x in columns for x in ['date', 'tarih', 'yearmonth', 'gün', 'day']):
        numeric_cols = df.select_dtypes(include=['number']).columns
        if len(numeric_cols) > 0:
            st.caption("📈 Zaman Grafiği")
            st.line_chart(df, y=numeric_cols)
            return

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
    
    # PROMPT GÜNCELLENDİ: Cihaz ve Metrik tanımları netleştirildi
    sys_prompt = f"""You are a GA4 API expert. TODAY: {today_str}.
Task: Convert user question to JSON for GA4 report.
RULES:
1. Output ONLY valid JSON. No markdown.
2. If user asks for 'devices', 'mobile', 'desktop' -> Use dimension: 'deviceCategory'.
3. If user asks for 'cities', 'location' -> Use dimension: 'city'.
4. Always include at least one METRIC (e.g., activeUsers, sessions, totalRevenue).
5. Always include at least one DIMENSION (e.g., date, deviceCategory, city).

Example JSON: 
{{
  "date_ranges": [{{"start_date": "30daysAgo", "end_date": "today"}}], 
  "dimensions": [{{"name": "deviceCategory"}}], 
  "metrics": [{{"name": "activeUsers"}}]
}}
"""
    full_prompt = f"{sys_prompt}\nReq: {prompt}"
    raw_text = ask_gemini_raw(full_prompt)
    try:
        match = re.search(r"\{[\s\S]*\}", raw_text)
        if match:
            clean_json = match.group(0)
            parsed = json.loads(clean_json)
            
            # --- EMNİYET KEMERİ (Bug Fix) ---
            # Eğer boyut (dimension) eksikse tarih ekle
            if "dimensions" not in parsed or not parsed["dimensions"]:
                parsed["dimensions"] = [{"name": "date"}]
            
            # Eğer metrik (metric) eksikse kullanıcı sayısı ekle
            if "metrics" not in parsed or not parsed["metrics"]:
                parsed["metrics"] = [{"name": "activeUsers"}]
                
            # Tarih aralığı eksikse bugünü ekle
            if "date_ranges" not in parsed: 
                parsed["date_ranges"] = [{"start_date": "today", "end_date": "today"}]
                
            return parsed, raw_text
        return None, raw_text
    except:
        return None, raw_text

def get_gemini_summary(df, prompt):
    data_sample = df.head(10).to_string()
    full_prompt = f"Kullanıcı Sorusu: '{prompt}'. \nGA4 Verisi:\n{data_sample}\n\nBu veriyi bir yöneticiye sunar gibi 2-3 cümleyle, emojiler kullanarak özetle. Trendleri vurgula."
    return ask_gemini_raw(full_prompt, temperature=0.7)

def run_ga4_report(prop_id, query):
    client = BetaAnalyticsDataClient(credentials=creds)
    
    # API'ye boş liste gitmesini engelle
    dims = query.get('dimensions', [])
    mets = query.get('metrics', [])
    
    if not dims: dims = [{"name": "date"}]
    if not mets: mets = [{"name": "activeUsers"}]

    dimensions = [{"name": d['name']} for d in dims]
    metrics = [{"name": m['name']} for m in mets]
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
        for i, met in enumerate(metrics): 
            val = row.metric_values[i].value
            try: item[met['name']] = float(val)
            except: item[met['name']] = val
        data.append(item)
    return pd.DataFrame(data)

def export_to_sheet(df, prompt_text):
    gc = gspread.authorize(creds)
    safe_title = str(prompt_text)[:20] if prompt_text else "Rapor"
    sh = gc.create(f"Rapor: {safe_title}")
    sh.sheet1.update_cell(1, 1, f"Soru: {prompt_text}")
    sh.sheet1.update([df.columns.values.tolist()] + df.values.tolist(), 'A3')
    sh.share(None, perm_type='anyone', role='reader')
    return sh.url

def get_ga4_properties():
    try:
        admin_client = AnalyticsAdminServiceClient(credentials=creds)
        results = []
        for account in admin_client.list_account_summaries():
            for property_summary in account.property_summaries:
                results.append({"Marka Adi": property_summary.display_name, "GA4_Property_ID": property_summary.property.split('/')[-1]})
        return pd.DataFrame(results)
    except: return pd.DataFrame()

# --- SIDEBAR & MARKA SEÇİMİ ---
with st.sidebar:
    st.title("⚙️ Kontrol")
    df_brands = get_ga4_properties()
    selected_brand_data = None
    
    if not df_brands.empty:
        brand_list = sorted(df_brands['Marka Adi'].tolist())
        selected_brand = st.selectbox("Marka Seç:", brand_list)
        selected_brand_data = df_brands[df_brands['Marka Adi'] == selected_brand].iloc[0]
        st.success(f"Aktif: {selected_brand}")
    else:
        st.error("Marka Bulunamadı")
    
    st.markdown("---")
    if st.button("🗑️ Sohbeti Temizle"):
        st.session_state.messages = []
        st.session_state.last_data = None
        st.rerun()

# --- ANA EKRAN ---
st.title("🤖 GA4 Asistanı")
st.caption("Verilerle sohbet edin. Grafik ve tablolar anında hazır.")

col1, col2, col3, col4 = st.columns(4)
quick_prompt = None
if col1.button("📅 Dün Durum?"): quick_prompt = "Dünkü toplam kullanıcı, oturum ve geliri getir"
if col2.button("📉 Son 1 Hafta"): quick_prompt = "Son 7 günün gün gün kullanıcı ve gelir değişimi"
if col3.button("🌍 Şehirler"): quick_prompt = "Geçen ay en çok gelen ilk 10 şehir (activeUsers)"
if col4.button("📱 Cihazlar"): quick_prompt = "Son 30 günde mobil ve desktop kullanım oranları (deviceCategory)"

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

prompt = st.chat_input("Merak ettiğin veriyi sor...")
if quick_prompt: prompt = quick_prompt

if prompt:
    if selected_brand_data is None:
        st.error("Lütfen soldan bir marka seçin.")
    else:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            message_placeholder = st.empty()
            with st.spinner("Veriler çekiliyor..."):
                query_json, raw_res = get_gemini_json(prompt)
                
                if query_json:
                    try:
                        df = run_ga4_report(str(selected_brand_data['GA4_Property_ID']), query_json)
                        if not df.empty:
                            summary = get_gemini_summary(df, prompt)
                            message_placeholder.markdown(summary)
                            auto_visualize(df)
                            with st.expander("Detaylı Tabloyu Gör"):
                                st.dataframe(df, use_container_width=True, hide_index=True)
                            st.session_state.messages.append({"role": "assistant", "content": summary})
                            st.session_state.last_data = df
                            st.session_state.last_prompt = prompt
                        else:
                            warn_msg = "Bu tarih aralığı veya kriter için veri bulunamadı (0 sonuç)."
                            message_placeholder.warning(warn_msg)
                            st.session_state.messages.append({"role": "assistant", "content": warn_msg})
                    except Exception as e:
                        st.error(f"Hata: {e}")
                else:
                    st.error("Soruyu anlayamadım, tekrar dener misin?")

if st.session_state.last_data is not None:
    st.markdown("---")
    col_dl1, col_dl2 = st.columns([1, 4])
    with col_dl1:
        if st.button("📂 Sheets'e Gönder"):
            with st.spinner("Google Sheets oluşturuluyor..."):
                url = export_to_sheet(st.session_state.last_data, st.session_state.last_prompt)
                st.success("Hazır!")
                st.markdown(f"[👉 Raporu Aç]({url})")
