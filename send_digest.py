import os, json, urllib.request, re
from config import DEFAULT_MODEL

for line in open('/tmp/hermy_repo/Projects/recherche/.env').read().splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        os.environ[k] = v

TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
MM_KEY = os.environ['MINIMAX_API_KEY']

def send(text):
    payload = json.dumps({'chat_id': str(CHAT_ID), 'text': text, 'disable_web_page_preview': True}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data=payload, headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

def summarize(title, abstract):
    prompt = f"""You are a scientific news editor. Write a concise 2-sentence French summary of this paper.

Title: {title}
Abstract: {abstract}

Rules:
- Write in French
- Max 2 sentences
- Focus on what makes this paper interesting
- No preamble"""

    payload = json.dumps({
        "model": DEFAULT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 200,
        "thinking_token": 0
    }).encode()

    req = urllib.request.Request(
        "https://api.minimax.io/v1/text/chatcompletion_v2",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {MM_KEY}"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
        content = data['choices'][0]['message']['content']
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        return content

papers = [
    {
        "title": "AI and ML in Clinical Medicine (2023)",
        "source": "NEJM / PubMed",
        "date": "2023",
        "url": "https://pubmed.ncbi.nlm.nih.gov/36988595/",
        "abstract": "Recent advances in AI/ML have enabled new applications in clinical medicine. This review examines the current state and future directions of AI and machine learning technologies in healthcare delivery, diagnosis, and treatment optimization."
    },
    {
        "title": "AI/ML in Musculoskeletal Physiotherapy",
        "source": "PubMed",
        "date": "2018",
        "url": "https://pubmed.ncbi.nlm.nih.gov/30502096/",
        "abstract": "This review outlines key applications of supervised and unsupervised machine learning in musculoskeletal medicine, including diagnostic imaging, patient measurement data, and clinical decision support."
    },
    {
        "title": "AI/ML in Lung Cancer Screening",
        "source": "PubMed",
        "date": "2023",
        "url": "https://pubmed.ncbi.nlm.nih.gov/37806742/",
        "abstract": "AI/ML tools can address challenges in lung cancer screening and improve health equity. Applications include eligibility determination, radiation dose reduction, lung nodule detection and classification."
    },
    {
        "title": "AI in Anesthesiology",
        "source": "PubMed",
        "date": "2020",
        "url": "https://pubmed.ncbi.nlm.nih.gov/31939856/",
        "abstract": "Six themes of AI in anesthesiology: depth of anesthesia monitoring, control of anesthesia, event/risk prediction, ultrasound guidance, pain management, and operating room logistics."
    },
    {
        "title": "ML in Gut Microbiome Multi-Omics",
        "source": "PubMed",
        "date": "2025",
        "url": "https://pubmed.ncbi.nlm.nih.gov/40118220/",
        "abstract": "AI/ML applied to multi-omics datasets enables analysis of complex gut microbiome data. Clinical applications include microbial biomarker discovery and treatment response prediction."
    },
    {
        "title": "ML in Surgical Research",
        "source": "PubMed",
        "date": "2023",
        "url": "https://pubmed.ncbi.nlm.nih.gov/36948720/",
        "abstract": "Machine learning is emerging in surgical research for predictive modeling in diagnostics, prognosis, operative timing, and surgical education across various subspecialties."
    }
]

header = "🤖 *Dr_NewPaper - Daily AI Research*\n━━━━━━━━━━━━━━━━━━\n📅 7 Mai 2026 | IA & Machine Learning\n🔍 6 articles свежие\n"
send(header)

for i, p in enumerate(papers, 1):
    try:
        summary = summarize(p['title'], p['abstract'])
    except Exception as e:
        summary = f"Resume indisponible ({e})"
    clean_url = re.sub(r'[\(\)]', '', p['url'])
    msg = f"{i}. {p['title']}\n   {p['source']} | {p['date']}\n   {summary}\n   {clean_url}"
    send(msg)
    print(f"Sent {i}: {p['title'][:50]}")

send("━━━━━━━━━━━━━━━━━━\nFin du digest IA | Prochain dans 24h")
print("Done!")
