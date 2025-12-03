import streamlit as st
import pandas as pd
import json
import gspread
from google.oauth2 import service_account
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest
from google.analytics.admin import AnalyticsAdminServiceClient
import google.generativeai as genai
import time

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="PlanB Whisperer", page_icon="💬", layout="wide")

# --- CSS (Görünüm) ---
st.markdown("""
<style>
    .stChatMessage {
        background-color: #ffffff;
        border-radius: 15px;
        padding: 10px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
    }
    [data-testid="stSidebar"] {
        background-color: #f8f9fa;
    }
</style>
""", unsafe_allow_html=True)

# --- AYARLAR VE GÜVENLİK ---
try:
    # Streamlit Secrets'tan bilgileri çek
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
    st.error("Sistem Ayarları Eksik (Secrets). Lütfen Streamlit panelinden yapılandırın.")
    st.stop()

# --- HAFIZA (Session State) ---
if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_data" not in st.session_state:
    st.session_state.last_data = None
if "last_prompt" not in st.session_state:
    st.session_state.last_prompt = ""

# --- FONKSİYONLAR ---

@st.cache_data(ttl=300)
def get_ga4_properties():
    """Otomatik Hesap Keşfi"""
    try:
        admin_client = AnalyticsAdminServiceClient(credentials=creds)
        results = []
        for account in admin_client.list_account_summaries(parent=""):
            for property_summary in account.property_summaries:
                results.append({
                    "Marka Adi": property_summary.display_name,
                    "GA4_Property_ID": property_summary.property.split('/')[-1]
                })
        return pd.DataFrame(results)
    except: return pd.DataFrame()

def get_gemini_json(prompt):
    model = genai.GenerativeModel('gemini-1.5-flash')
    sys_prompt = """Sen GA4 Data API uzmanısın. Kullanıcı sorusunu JSON'a çevir. 
    Metrics, dimensions, dateRanges, limit kullan.
    Sadece JSON döndür. Markdown yok.
    Örnek: {"date_ranges": [{"start_date": "30daysAgo", "end_date": "yesterday"}], "dimensions": [{"name": "itemAccountName"}], "metrics": [{"name": "itemsPurchased"}]}
    """
    try:
        res = model.generate_content(f"{sys_prompt}\nSoru: {prompt}")
        return json.loads(res.text.replace("```json", "").replace("```", "").strip())
    except: return None

def get_gemini_summary(df, prompt):
    """Veriyi yorumlayan yapay zeka"""
    model = genai.GenerativeModel('gemini-1.5-flash')
    data_sample = df.head(10).to_string()
    sys_prompt = f"Kullanıcı şunu sordu: '{prompt}'. Elimdeki GA4 verisi şu:\n{data_sample}\n\nBu veriye bakarak kullanıcıya 1-2 cümlelik samimi, net bir özet cevap ver. Rakamları yuvarlayabilirsin."
    res = model.generate_content(sys_prompt)
    return res.text

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

# 1. Yan Menü
with st.sidebar:
    st.title("PlanB 💬")
    st.caption("Veri Ajanı")
    
    df_brands = get_ga4_properties()
    selected_brand_data = None
    
    if not df_brands.empty:
        # Alfabetik sırala
        brand_list = sorted(df_brands['Marka Adi'].tolist())
        selected_brand = st.selectbox("Marka Seç:", brand_list)
        selected_brand_data = df_brands[df_brands['Marka Adi'] == selected_brand].iloc[0]
        st.success(f"✅ {selected_brand} Bağlı")
        
        if st.button("🗑️ Temizle"):
            st.session_state.messages = []
            st.rerun()
    else:
        st.warning("⚠️ Marka bulunamadı. Robot mailini GA4 hesaplarına eklediniz mi?")

# 2. Sohbet Akışı
st.subheader("Veri Asistanı")

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# 3. Input ve İşlem
if prompt := st.chat_input("Soru sor... (Örn: Geçen ay en çok satan 5 ürün?)"):
    if not selected_brand_data:
        st.error("Lütfen önce bir marka seçin.")
    else:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Analiz yapılıyor..."):
                query_json = get_gemini_json(prompt)
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
                            st.warning("Veri bulunamadı.")
                    except Exception as e:
                        st.error(f"Hata: {e}")

# 4. Export Butonu (Son veri varsa göster)
if st.session_state.last_data is not None:
    if st.button("📂 Bu Tabloyu Google Sheets'e Aktar"):
        with st.spinner("Oluşturuluyor..."):
            url = export_to_sheet(st.session_state.last_data, st.session_state.last_prompt)
            st.success("Tamamlandı!")
            st.markdown(f"[👉 Dosyayı Aç]({url})")