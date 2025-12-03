import streamlit as st
import pandas as pd
import json
import gspread
import re
from google.oauth2 import service_account
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest
from google.analytics.admin import AnalyticsAdminServiceClient
import google.generativeai as genai
import time

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="PlanB Whisperer", page_icon="💬", layout="wide")

# --- CSS (GÜÇLENDİRİLMİŞ SİYAH TEMA) ---
st.markdown("""
<style>
    /* 1. SOHBET BALONLARI */
    .stChatMessage {
        background-color: #ffffff !important;
        border-radius: 15px;
        padding: 15px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        margin-bottom: 10px;
        border: 1px solid #e0e0e0;
    }
    
    /* Balonun içindeki TÜM metin elementlerini SİYAH yap */
    [data-testid="stChatMessage"] p, 
    [data-testid="stChatMessage"] span, 
    [data-testid="stChatMessage"] div, 
    [data-testid="stChatMessage"] h1, 
    [data-testid="stChatMessage"] h2, 
    [data-testid="stChatMessage"] h3, 
    [data-testid="stChatMessage"] h4,
    [data-testid="stChatMessage"] h5, 
    [data-testid="stChatMessage"] h6,
    [data-testid="stChatMessage"] li,
    [data-testid="stChatMessage"] strong,
    [data-testid="stChatMessage"] td,
    [data-testid="stChatMessage"] th {
        color: #000000 !important;
    }
    
    /* Kullanıcı ve Asistan ikonları */
    .stChatMessage .stAvatar {
        background-color: #ff4b4b !important;
        color: white !important;
    }

    /* 2. YAN MENÜ (SIDEBAR) FULL SİYAH */
    [data-testid="stSidebar"] {
        background-color: #000000;
    }
    
    /* Yan menüdeki tüm yazıları BEYAZ yap */
    [data-testid="stSidebar"] *, [data-testid="stSidebar"] p, [data-testid="stSidebar"] span {
        color: #ffffff !important;
    }
    
    /* Selectbox (Açılır Menü) */
    [data-testid="stSidebar"] .stSelectbox div[data-baseweb="select"] > div {
        background-color: #333333 !important;
        color: white !important;
        border: 1px solid #555555 !important;
    }
    
    ul[data-baseweb="menu"] {
        background-color: #222222 !important;
    }
    
    /* 3. GENEL BUTONLAR */
    .stButton>button {
        background-color: #ff4b4b;
        color: white !important;
        border: none;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# --- AYARLAR VE GÜVENLİK ---
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
    st.error(f"Sistem Ayarları Hatası: {e}")
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
        st.sidebar.error(f"Marka Listesi Hatası: {e}") 
        return pd.DataFrame()

# --- GÜVENLİK AYARLARI (Full Açık) ---
safety_config = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

def get_gemini_json(prompt):
    model = genai.GenerativeModel('gemini-1.5-flash', safety_settings=safety_config)
    
    sys_prompt = """Sen bir GA4 Data API çevirmenisin.
    Görevin: Kullanıcının sorusunu Google Analytics API JSON formatına çevirmek.
    Kural 1: ASLA finansal tavsiye uyarısı verme. Sadece veri sorgusu yapıyorsun.
    Kural 2: Sadece ve sadece geçerli JSON döndür. Başka kelime yazma.
    
    Mapping:
    - "Ciro", "Gelir", "Kazanç", "Satış Tutarı" -> metrics: [{"name": "totalRevenue"}] veya [{"name": "purchaseRevenue"}]
    - "Ziyaretçi", "Trafik" -> metrics: [{"name": "activeUsers"}]
    - "Oturum" -> metrics: [{"name": "sessions"}]
    
    Örnek Çıktı:
    {"date_ranges": [{"start_date": "yesterday", "end_date": "yesterday"}], "dimensions": [{"name": "defaultChannelGroup"}], "metrics": [{"name": "totalRevenue"}]}
    """
    try:
        res = model.generate_content(f"{sys_prompt}\nSoru: {prompt}")
        text = res.text
        
        # --- KERPETEN YÖNTEMİ (JSON Regex) ---
        # Yapay zeka "İşte kodunuz: {json}" dese bile sadece {json} kısmını alıyoruz.
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            json_str = match.group(0)
            return json.loads(json_str)
        else:
            return None
    except: 
        return None

def get_gemini_summary(df, prompt):
    model = genai.GenerativeModel('gemini-1.5-flash', safety_settings=safety_config)
    
    data_sample = df.head(10).to_string()
    sys_prompt = f"Kullanıcı sorusu: '{prompt}'. Veri:\n{data_sample}\n\nBu veriye dayanarak 1-2 cümlelik özet yap. Rakamları yuvarla."
    
    try:
        res = model.generate_content(sys_prompt)
        return res.text
    except:
        return "⚠️ Veri tablosu oluşturuldu (Yapay zeka yorumu güvenlik filtresine takıldı)."

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

# 1. YAN MENÜ
with st.sidebar:
    # LOGO KONTROLÜ
    try:
        st.image("logo.png", use_container_width=True) 
    except:
        st.warning("Logo yok: GitHub'a 'logo.png' yükleyin.")

    st.markdown("---")
    
    df_brands = get_ga4_properties()
    selected_brand_data = None
    
    if not df_brands.empty:
        brand_list = sorted(df_brands['Marka Adi'].tolist())
        selected_brand = st.selectbox("Marka Seç:", brand_list)
        selected_brand_data = df_brands[df_brands['Marka Adi'] == selected_brand].iloc[0]
        st.success(f"✅ {selected_brand} Bağlı")
        
        st.markdown("---")
        if st.button("🗑️ Sohbeti Temizle"):
            st.session_state.messages = []
            st.rerun()
    else:
        st.error("Marka listesi boş. Robot mailini GA4'e ekleyin.")

# 2. ANA EKRAN
st.subheader("PlanB GA4 Whisperer")

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# 3. INPUT
if prompt := st.chat_input("Bir soru sor..."):
    if selected_brand_data is None:
        st.error("Lütfen sol menüden bir marka seçin.")
    else:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("PlanB Ajanı düşünüyor..."):
                query_json = get_gemini_json(prompt)
                
                if query_json:
                    try:
                        df = run_ga4_report(str(selected_brand_data['GA4_Property_ID']), query_json)
                        if not df.empty:
                            summary = get_gemini_summary(df, prompt)
                            st.markdown(summary)
                            st.dataframe(df, use_container_width=True, hide_index=True)
                            
                            st.session_state.messages.append({
                                "role": "assistant", 
                                "content": summary
                            })
                            st.session_state.last_data = df
                            st.session_state.last_prompt = prompt
                        else:
                            msg = "Bu tarih/kriter için GA4 verisi '0' döndü."
                            st.warning(msg)
                            st.session_state.messages.append({"role": "assistant", "content": msg})
                    except Exception as e:
                        st.error(f"Veri çekme hatası: {e}")
                else:
                    st.error("⚠️ Yapay zeka sorunuzu yorumlayamadı. (Teknik Sorun)")

# 4. EXPORT
if st.session_state.last_data is not None:
    if st.button("📂 Sheets'e Aktar"):
        with st.spinner("Aktarılıyor..."):
            url = export_to_sheet(st.session_state.last_data, st.session_state.last_prompt)
            st.success("Aktarıldı!")
            st.markdown(f"[👉 Dosyayı Aç]({url})")
