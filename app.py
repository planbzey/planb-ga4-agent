import streamlit as st
import pandas as pd
import json
import gspread
import re
import datetime 
import requests 
import time
from google.oauth2 import service_account
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest
from google.analytics.admin import AnalyticsAdminServiceClient

# --- 1. SAYFA AYARLARI (EN BAŞTA) ---
st.set_page_config(page_title="PlanB Whisperer", page_icon="🔒", layout="wide")

# ==========================================
# 🔐 GÜVENLİK DUVARI
# ==========================================
def check_password():
    """Şifre kontrolü yapar."""
    def password_entered():
        if st.session_state["password"] == st.secrets["general"]["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("🔑 Lütfen Giriş Şifresini Yazın:", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("🔑 Lütfen Giriş Şifresini Yazın:", type="password", on_change=password_entered, key="password")
        st.error("😕 Hatalı şifre.")
        return False
    else:
        return True

if not check_password():
    st.stop()

# ==========================================
# 🎨 TASARIM VE CSS (GÖRÜNÜM AYARLARI)
# ==========================================
st.markdown("""
<style>
    /* Genel Arka Plan */
    .stApp { background-color: #f8f9fa; }
    
    /* Sohbet Baloncukları */
    .stChatMessage {
        background-color: #ffffff !important;
        border-radius: 20px;
        padding: 15px;
        margin-bottom: 10px;
        border: 1px solid #e0e0e0;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
    }
    
    /* Yazı Renkleri - SİYAH ZORLAMA */
    .stChatMessage p, .stChatMessage li, .stChatMessage div {
        color: #000000 !important;
    }

    /* Kullanıcı Mesajı (Mavi) */
    [data-testid="stChatMessage"][data-testid="user"] {
        background-color: #e3f2fd !important;
    }

    /* Sidebar (Koyu Tema) */
    [data-testid="stSidebar"] { background-color: #1e1e1e; }
    [data-testid="stSidebar"] *, [data-testid="stSidebar"] p { color: #e0e0e0 !important; }
    
    /* Hızlı Butonlar */
    .stButton>button {
        border-radius: 12px;
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
</style>
""", unsafe_allow_html=True)

# --- AYARLAR VE API ---
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
if "messages" not in st.session_state: st.session_state.messages = []
if "last_data" not in st.session_state: st.session_state.last_data = None
if "active_model_name" not in st.session_state: st.session_state.active_model_name = None

# --- FONKSİYONLAR ---

def auto_visualize(df):
    columns = [c.lower() for c in df.columns]
    cat_cols = df.select_dtypes(include=['object']).columns
    num_cols = df.select_dtypes(include=['number']).columns
    
    if len(num_cols) == 0: return

    # Zaman Grafiği
    if any(x in columns for x in ['date', 'tarih', 'yearmonth', 'gün']):
        st.caption("📈 Zaman Grafiği")
        st.line_chart(df, y=num_cols)
        return

    # Kategorik Dağılım
    if len(cat_cols) > 0:
        st.caption(f"📊 {cat_cols[0]} Dağılımı")
        st.bar_chart(df, x=cat_cols[0], y=num_cols[0])

def find_best_model():
    if st.session_state.active_model_name: return st.session_state.active_model_name, None
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    try:
        resp = requests.get(url).json()
        if "models" in resp:
            valid = [m["name"].replace("models/", "") for m in resp['models'] if "gemini" in m["name"] and "generateContent" in m.get("supportedGenerationMethods", [])]
            if valid:
                selected = next((m for m in valid if "flash" in m), valid[0])
                st.session_state.active_model_name = selected
                return selected, None
    except: pass
    return "gemini-1.5-flash", None

def ask_gemini_raw(prompt_text, temperature=0.0):
    model, _ = find_best_model()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    data = {"contents": [{"parts": [{"text": prompt_text}]}], "generationConfig": {"temperature": temperature}}
    try:
        res = requests.post(url, headers=headers, json=data)
        if res.status_code == 200: return res.json()['candidates'][0]['content']['parts'][0]['text']
    except: pass
    return "Error"

def get_gemini_json_with_history(current_prompt, history_messages):
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    
    history_text = ""
    for msg in history_messages[-4:]: 
        role = "User" if msg["role"] == "user" else "Assistant"
        content = msg["content"]
        if isinstance(content, str):
             history_text += f"{role}: {content}\n"

    sys_prompt = f"""You are a GA4 Expert. TODAY: {today_str}.
    HISTORY: {history_text}
    REQUEST: {current_prompt}
    TASK:
    1. If user asks for calculation/conversion on old data -> Output "CALCULATION_NEEDED".
    2. If new data -> Output JSON.
    3. If dimensions missing -> use 'date'. If metrics missing -> use 'activeUsers'.
    Example: {{"date_ranges": [{{"start_date": "today", "end_date": "today"}}], "dimensions": [{{"name": "date"}}], "metrics": [{{"name": "activeUsers"}}]}}
    """
    raw_text = ask_gemini_raw(sys_prompt)
    if "CALCULATION_NEEDED" in raw_text: return "CALC", raw_text

    try:
        match = re.search(r"\{[\s\S]*\}", raw_text)
        if match:
            parsed = json.loads(match.group(0))
            if "dimensions" not in parsed: parsed["dimensions"] = [{"name": "date"}]
            if "metrics" not in parsed: parsed["metrics"] = [{"name": "activeUsers"}]
            if "date_ranges" not in parsed: parsed["date_ranges"] = [{"start_date": "today", "end_date": "today"}]
            return parsed, raw_text
    except: pass
    return None, raw_text

def get_gemini_chat_response(prompt, history_messages, last_data_summary):
    history_text = ""
    for msg in history_messages[-4:]:
         if isinstance(msg["content"], str):
            history_text += f"{msg['role']}: {msg['content']}\n"
    
    full_prompt = f"CONTEXT: Previous Data: {last_data_summary}\nHISTORY: {history_text}\nUSER: {prompt}\nTask: Answer the user's calculation/conversion question based on previous data. Be helpful."
    return ask_gemini_raw(full_prompt, temperature=0.7)

def get_gemini_summary(df, prompt):
    data_sample = df.head(10).to_string()
    return ask_gemini_raw(f"Soru: {prompt}\nData:\n{data_sample}\n\nÖzetle (emoji kullan):", temperature=0.7)

def run_ga4_report(prop_id, query):
    client = BetaAnalyticsDataClient(credentials=creds)
    req = RunReportRequest(
        property=f"properties/{prop_id}",
        dimensions=[{"name": d['name']} for d in query.get('dimensions', [])],
        metrics=[{"name": m['name']} for m in query.get('metrics', [])],
        date_ranges=[query['date_ranges'][0]],
        limit=100
    )
    res = client.run_report(req)
    data = []
    for row in res.rows:
        item = {}
        for i, dim in enumerate(query.get('dimensions', [])): item[dim['name']] = row.dimension_values[i].value
        for i, met in enumerate(query.get('metrics', [])): 
            try: item[met['name']] = float(row.metric_values[i].value)
            except: item[met['name']] = row.metric_values[i].value
        data.append(item)
    return pd.DataFrame(data)

def get_ga4_properties():
    try:
        admin = AnalyticsAdminServiceClient(credentials=creds)
        results = []
        for account in admin.list_account_summaries():
            for property_summary in account.property_summaries:
                results.append({"Marka Adi": property_summary.display_name, "GA4_Property_ID": property_summary.property.split('/')[-1]})
        return pd.DataFrame(results)
    except: return pd.DataFrame()

# --- SIDEBAR (HESAP AYARLARI GERİ GELDİ) ---
with st.sidebar:
    st.title("⚙️ Kontrol Paneli")
    
    # Model Bilgisi
    model_name, err = find_best_model()
    if not err: st.caption(f"🧠 Zeka: {model_name}")

    # Marka Seçimi (Tamir Edildi)
    df_brands = get_ga4_properties()
    selected_brand_data = None
    
    if not df_brands.empty:
        brand_list = sorted(df_brands['Marka Adi'].tolist())
        selected_brand = st.selectbox("Marka / Mülk Seç:", brand_list)
        selected_brand_data = df_brands[df_brands['Marka Adi'] == selected_brand].iloc[0]
        st.success(f"✅ Seçildi: {selected_brand}")
    else:
        st.error("Marka/Mülk Bulunamadı (Yetki Kontrolü Yapın)")
    
    st.markdown("---")
    if st.button("🗑️ Sohbeti Temizle"):
        st.session_state.messages = []
        st.session_state.last_data = None
        st.rerun()
        
    if st.button("🔒 Güvenli Çıkış"):
        del st.session_state["password_correct"]
        st.rerun()

# --- ANA EKRAN VE BUTONLAR ---
st.title("🤖 GA4 Asistanı")
st.caption("Verilerle konuşun, hesap yaptırın, grafik çizdirin.")

# Hızlı Butonlar (Geri Geldi!)
col1, col2, col3, col4 = st.columns(4)
quick_prompt = None
if col1.button("📅 Dün Durum?"): quick_prompt = "Dünkü toplam kullanıcı, oturum ve geliri getir"
if col2.button("📉 Bu Hafta"): quick_prompt = "Son 7 günün gün gün kullanıcı ve gelir değişimi"
if col3.button("🌍 Şehirler"): quick_prompt = "Geçen ay en çok gelen ilk 10 şehir (activeUsers)"
if col4.button("📱 Cihazlar"): quick_prompt = "Son 30 günde mobil ve desktop kullanım oranları (deviceCategory)"

# Sohbet Akışı
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]): st.markdown(msg["content"])

# Giriş
prompt = st.chat_input("Bir soru sor...")
if quick_prompt: prompt = quick_prompt

if prompt:
    if not selected_brand_data: st.error("Lütfen soldan bir marka seçin.")
    else:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"): st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Analiz yapılıyor..."):
                query_json, raw_res = get_gemini_json_with_history(prompt, st.session_state.messages)
                
                # Sadece Sohbet/Hesap
                if query_json == "CALC":
                    last_sum = st.session_state.messages[-2]["content"] if len(st.session_state.messages)>1 else ""
                    resp = get_gemini_chat_response(prompt, st.session_state.messages, last_sum)
                    st.markdown(resp)
                    st.session_state.messages.append({"role": "assistant", "content": resp})
                
                # Veri Çekme
                elif query_json:
                    try:
                        df = run_ga4_report(str(selected_brand_data['GA4_Property_ID']), query_json)
                        if not df.empty:
                            summary = get_gemini_summary(df, prompt)
                            st.markdown(summary)
                            auto_visualize(df)
                            with st.expander("Tablo Verisi"): st.dataframe(df, use_container_width=True)
                            st.session_state.messages.append({"role": "assistant", "content": summary})
                            st.session_state.last_data = df
                        else:
                            st.warning("Veri yok.")
                    except Exception as e: st.error(f"Hata: {e}")
                else:
                    st.error("Anlayamadım.")
