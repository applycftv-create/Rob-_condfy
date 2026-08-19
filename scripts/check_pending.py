"""
Robô de monitoramento de solicitações faciais pendentes no Condfy.

Faz login no painel web.condfy.com.br, visita uma lista de URLs de
"Solicitações > Facial" e verifica se existem pessoas com status
"Pendente". Novas pendências (ainda não notificadas anteriormente)
são enviadas por e-mail. O estado de o-que-já-foi-notificado é
persistido em um arquivo JSON (STATE_FILE) para não notificar a
mesma pendência repetidamente a cada execução.

Variáveis de ambiente esperadas (podem vir de um arquivo .env na raiz do projeto):
  CONDFY_USERNAME, CONDFY_PASSWORD  - credenciais de login do Condfy
  GMAIL_USER, GMAIL_APP_PASSWORD    - conta Gmail usada para enviar o e-mail
  NOTIFY_EMAIL                      - destinatário(s) da notificação por e-mail
  WHATSAPP_RECIPIENTS (opcional) - destinatário(s) via CallMeBot, formato
                                    "telefone:apikey", um ou mais separados
                                    por vírgula/quebra de linha. Se vazio,
                                    o envio por WhatsApp é pulado.
  URLS_FILE   (opcional) - caminho do arquivo com as URLs (padrão: config/urls.txt)
  STATE_FILE  (opcional) - caminho do arquivo de estado (padrão: state/notified.json)
  DEBUG_DIR   (opcional) - pasta para salvar screenshot/HTML em caso de erro (padrão: debug_artifacts)
"""

import json
import os
import re
import smtplib
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
URLS_FILE = Path(os.environ.get("URLS_FILE", ROOT / "config" / "urls.txt"))
STATE_FILE = Path(os.environ.get("STATE_FILE", ROOT / "state" / "notified.json"))
CLIENTS_FILE = Path(os.environ.get("CLIENTS_FILE", ROOT / "config" / "clients.json"))
DEBUG_DIR = Path(os.environ.get("DEBUG_DIR", ROOT / "debug_artifacts"))

DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}")
LICENSE_RE = re.compile(r"/licencas/(\d+)/")
STATE_MAX_AGE_DAYS = 60

# Textos de interface que não são nomes de pessoas — se a extração acabar
# capturando um desses como "nome" (ex: o dropdown de filtro), descartamos a
# linha em vez de notificar algo que não é uma pendência real.
UI_CHROME_NAMES = {
    "filtrar por status",
    "filtrar por operação",
    "filtrar por operacao",
    "total de solicitações",
    "total de solicitacoes",
}

# JS injetado na página: localiza cada "linha" da lista pelo badge de status
# "Pendente" e sobe pelos ancestrais até achar um bloco que também contenha
# uma data (o que indica que chegamos ao contêiner da linha inteira).
# Essa abordagem evita depender de nomes de classes/IDs específicos do Condfy,
# que não pudemos inspecionar diretamente (acesso à internet bloqueado neste
# ambiente de desenvolvimento).
EXTRACT_ROWS_JS = r"""
() => {
  const dateRe = /\d{2}\/\d{2}\/\d{4}\s+\d{2}:\d{2}/;
  // Exclui o valor exibido pelo dropdown "Filtrar por status" (que também
  // mostra o texto "Pendente" quando esse é o filtro selecionado) — só nos
  // interessam badges de status dentro das linhas da lista, não o filtro.
  const isInsideDropdown = (el) =>
    !!el.closest('[role="combobox"], [role="button"], [aria-haspopup], select, [role="listbox"], [role="option"]');
  const isPendingBadge = (el) =>
    el.children.length === 0 &&
    el.textContent.trim().toLowerCase() === 'pendente' &&
    !isInsideDropdown(el);
  const badges = Array.from(document.querySelectorAll('*')).filter(isPendingBadge);
  const rows = [];
  const seen = new Set();
  for (const badge of badges) {
    let node = badge;
    let rowNode = null;
    for (let i = 0; i < 10 && node; i++) {
      node = node.parentElement;
      if (!node) break;
      const text = node.innerText || '';
      if (dateRe.test(text) && text.length < 2000) {
        rowNode = node;
        break;
      }
    }
    if (rowNode && !seen.has(rowNode)) {
      seen.add(rowNode);
      rows.push(rowNode.innerText.trim());
    }
  }
  return rows;
}
"""


def load_client_names():
    if CLIENTS_FILE.exists():
        return json.loads(CLIENTS_FILE.read_text(encoding="utf-8") or "{}")
    return {}


def load_urls():
    urls = []
    for line in URLS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    if not urls:
        raise RuntimeError(f"Nenhuma URL encontrada em {URLS_FILE}")
    return urls


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8") or "{}")
    return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def prune_state(state, now):
    cutoff = now - timedelta(days=STATE_MAX_AGE_DAYS)
    kept = {}
    for key, notified_at in state.items():
        try:
            when = datetime.fromisoformat(notified_at)
        except ValueError:
            continue
        if when >= cutoff:
            kept[key] = notified_at
    return kept


def is_login_form_visible(page):
    try:
        return page.locator("input[type='password']").first.is_visible(timeout=3000)
    except PlaywrightTimeoutError:
        return False


def smart_fill(page, selectors, value):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=1500):
                locator.fill(value)
                return True
        except PlaywrightTimeoutError:
            continue
    return False


def do_login(page, username, password):
    email_selectors = [
        "input[type='email']",
        "input[name*='email' i]",
        "input[name*='user' i]",
        "input[placeholder*='mail' i]",
        "input[placeholder*='usu' i]",
    ]
    password_selectors = ["input[type='password']"]

    if not smart_fill(page, email_selectors, username):
        raise RuntimeError("Não foi possível localizar o campo de e-mail/usuário na tela de login.")
    print(f"[debug] campo de usuário/e-mail preenchido (comprimento={len(username)})")
    # Tab para disparar blur/validação de formulários React/MUI antes do próximo campo.
    page.keyboard.press("Tab")

    if not smart_fill(page, password_selectors, password):
        raise RuntimeError("Não foi possível localizar o campo de senha na tela de login.")
    print(f"[debug] campo de senha preenchido (comprimento={len(password)})")
    page.keyboard.press("Tab")

    submit_selectors = [
        "button[type='submit']",
        "button:has-text('Entrar')",
        "button:has-text('Acessar')",
        "button:has-text('Login')",
    ]
    submit_locator = None
    for selector in submit_selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=1500):
                submit_locator = locator
                break
        except PlaywrightTimeoutError:
            continue

    if submit_locator is None:
        page.locator("input[type='password']").first.press("Enter")
    else:
        # Alguns formulários (ex: MUI) mantêm o botão "disabled" até a validação
        # do lado do cliente rodar; espera um pouco antes de clicar.
        for _ in range(10):
            if submit_locator.get_attribute("disabled") is None:
                break
            page.wait_for_timeout(300)
        else:
            print("[aviso] botão de login permaneceu desabilitado após preencher os campos.", file=sys.stderr)
            save_debug_artifacts(page, "login_button_still_disabled")
        submit_locator.click(timeout=5000)

    # Captura logo após o clique, antes de um possível reload que "engoliria"
    # um toast/alerta de erro antes que déssemos tempo de vê-lo.
    page.wait_for_timeout(2000)
    save_debug_artifacts(page, "login_immediate_after_click")

    page.wait_for_load_state("networkidle", timeout=20000)

    if is_login_form_visible(page):
        save_debug_artifacts(page, "login_failed_after_submit")
        raise RuntimeError(
            "Login não teve sucesso: o formulário de login ainda está visível após submeter. "
            "Verifique CONDFY_USERNAME/CONDFY_PASSWORD e os artefatos de debug "
            "'login_failed_after_submit.png/html'."
        )


def save_debug_artifacts(page, label):
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        safe_label = re.sub(r"[^a-zA-Z0-9_-]", "_", label)
        page.screenshot(path=str(DEBUG_DIR / f"{safe_label}.png"), full_page=True)
        (DEBUG_DIR / f"{safe_label}.html").write_text(page.content(), encoding="utf-8")
    except Exception as exc:  # melhor esforço; não deve interromper o fluxo principal
        print(f"[aviso] falha ao salvar artefatos de debug para {label}: {exc}", file=sys.stderr)


def parse_row(row_text, license_id, url):
    lines = [line.strip() for line in row_text.split("\n") if line.strip()]
    name = lines[0] if lines else "(nome não identificado)"
    date_match = DATE_RE.search(row_text)
    date_str = date_match.group(0) if date_match else "(data não identificada)"
    operation = "(operação não identificada)"
    for candidate in ("Inclusão", "Exclusão", "Alteração"):
        if candidate in row_text:
            operation = candidate
            break
    key = f"{license_id}|{name}|{operation}|{date_str}"
    return {
        "key": key,
        "license_id": license_id,
        "name": name,
        "operation": operation,
        "date": date_str,
        "url": url,
    }


def collect_pending(page, url):
    license_match = LICENSE_RE.search(url)
    license_id = license_match.group(1) if license_match else "desconhecida"

    page.goto(url, wait_until="networkidle", timeout=30000)

    if is_login_form_visible(page):
        raise RuntimeError(f"Sessão não autenticada ao acessar {url} (formulário de login reapareceu).")

    row_texts = page.evaluate(EXTRACT_ROWS_JS)
    pending = [parse_row(text, license_id, url) for text in row_texts]
    pending = [item for item in pending if item["name"].strip().lower() not in UI_CHROME_NAMES]

    total_match = re.search(r"Total de solicita[çc][õo]es:\s*(\d+)", page.content())
    if total_match and int(total_match.group(1)) > 0 and not pending:
        print(
            f"[aviso] {url} reporta total > 0 mas nenhuma linha 'Pendente' foi extraída; "
            "a estrutura da página pode ter mudado. Verifique os artefatos de debug.",
            file=sys.stderr,
        )
        save_debug_artifacts(page, f"license_{license_id}_zero_extracted")

    return pending


def parse_recipients(raw):
    """Aceita um ou mais e-mails separados por vírgula, ponto-e-vírgula e/ou quebras de linha."""
    recipients = [addr.strip() for addr in re.split(r"[,;\n]+", raw) if addr.strip()]
    if not recipients:
        raise RuntimeError("NOTIFY_EMAIL está vazio.")
    return recipients


OPERATION_COLORS = {
    "Inclusão": "#059669",
    "Exclusão": "#dc2626",
    "Alteração": "#d97706",
}
DEFAULT_OPERATION_COLOR = "#6b7280"


def group_by_client(new_items, client_names):
    groups = {}
    for item in new_items:
        client_name = client_names.get(item["license_id"], f"Licença {item['license_id']}")
        groups.setdefault(client_name, []).append(item)
    return dict(sorted(groups.items(), key=lambda pair: pair[0].lower()))


def build_plain_text_body(grouped):
    lines = ["Foram encontradas novas pendências de aprovação facial no Condfy:", ""]
    for client_name, items in grouped.items():
        lines.append(f"== {client_name} ==")
        for item in items:
            lines.append(f"- {item['name']} | {item['operation']} | {item['date']}")
            lines.append(f"  {item['url']}")
        lines.append("")
    return "\n".join(lines)


def build_html_body(grouped, total_count):
    client_sections = []
    for client_name, items in grouped.items():
        rows = []
        for item in items:
            color = OPERATION_COLORS.get(item["operation"], DEFAULT_OPERATION_COLOR)
            rows.append(f"""
              <tr>
                <td style="padding:10px 0;border-bottom:1px solid #eef0f3;">
                  <div style="font-size:14px;font-weight:600;color:#111827;">{escape(item['name'])}</div>
                  <div style="font-size:12px;color:#6b7280;margin-top:2px;">{escape(item['date'])}</div>
                </td>
                <td style="padding:10px 0;border-bottom:1px solid #eef0f3;text-align:right;white-space:nowrap;">
                  <span style="display:inline-block;padding:2px 8px;border-radius:10px;background:{color}1a;color:{color};font-size:11px;font-weight:600;">
                    {escape(item['operation'])}
                  </span>
                  <div style="margin-top:6px;">
                    <a href="{escape(item['url'])}" style="font-size:12px;color:#2563eb;text-decoration:none;">Ver solicitação →</a>
                  </div>
                </td>
              </tr>
            """)
        client_sections.append(f"""
          <div style="margin-bottom:24px;">
            <div style="font-size:15px;font-weight:700;color:#111827;padding-bottom:8px;margin-bottom:4px;border-bottom:2px solid #111827;">
              {escape(client_name)}
              <span style="font-weight:400;color:#6b7280;font-size:13px;">({len(items)} pendente(s))</span>
            </div>
            <table style="width:100%;border-collapse:collapse;">
              {''.join(rows)}
            </table>
          </div>
        """)

    return f"""
    <div style="margin:0;padding:24px;background:#f4f5f7;font-family:Arial,Helvetica,sans-serif;color:#1a1a1a;">
      <div style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb;">
        <div style="background:#111827;padding:20px 24px;">
          <span style="color:#ffffff;font-size:17px;font-weight:700;">Condfy · Pendências de aprovação facial</span>
        </div>
        <div style="padding:24px;">
          <p style="margin:0 0 20px;font-size:14px;color:#4b5563;">
            Foram encontradas <strong>{total_count}</strong> nova(s) pendência(s) de aprovação facial:
          </p>
          {''.join(client_sections)}
        </div>
        <div style="padding:14px 24px;background:#f9fafb;border-top:1px solid #e5e7eb;font-size:11px;color:#9ca3af;">
          Robô Condfy — verificação automática a cada 20 minutos.
        </div>
      </div>
    </div>
    """


def send_email(new_items):
    gmail_user = os.environ["GMAIL_USER"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]
    recipients = parse_recipients(os.environ["NOTIFY_EMAIL"])
    client_names = load_client_names()

    grouped = group_by_client(new_items, client_names)
    subject = f"Condfy: {len(new_items)} pendência(s) de aprovação facial"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(build_plain_text_body(grouped), "plain", "utf-8"))
    msg.attach(MIMEText(build_html_body(grouped, len(new_items)), "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_app_password)
        server.sendmail(gmail_user, recipients, msg.as_string())


CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"
CALLMEBOT_MAX_MESSAGE_LENGTH = 2000
CALLMEBOT_DELAY_BETWEEN_SENDS = 3  # segundos, evita rate-limit da API


def parse_whatsapp_recipients(raw):
    """Formato: telefone:apikey — um ou mais, separados por vírgula/;/quebra de linha."""
    recipients = []
    for entry in re.split(r"[,;\n]+", raw):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            print(
                f"[aviso] entrada inválida em WHATSAPP_RECIPIENTS (esperado telefone:apikey): {entry}",
                file=sys.stderr,
            )
            continue
        phone, apikey = entry.split(":", 1)
        recipients.append((phone.strip(), apikey.strip()))
    return recipients


def build_whatsapp_text(grouped, total_count):
    lines = [f"*Condfy* — {total_count} pendência(s) de aprovação facial:", ""]
    for client_name, items in grouped.items():
        lines.append(f"*{client_name}* ({len(items)})")
        for item in items:
            lines.append(f"- {item['name']} | {item['operation']} | {item['date']}")
        lines.append("")
    text = "\n".join(lines).strip()
    if len(text) > CALLMEBOT_MAX_MESSAGE_LENGTH:
        text = text[: CALLMEBOT_MAX_MESSAGE_LENGTH - 20] + "\n... (lista truncada)"
    return text


def send_whatsapp(new_items):
    raw = os.environ.get("WHATSAPP_RECIPIENTS", "").strip()
    if not raw:
        return

    recipients = parse_whatsapp_recipients(raw)
    if not recipients:
        return

    client_names = load_client_names()
    grouped = group_by_client(new_items, client_names)
    text = build_whatsapp_text(grouped, len(new_items))

    for index, (phone, apikey) in enumerate(recipients):
        params = urllib.parse.urlencode({"phone": phone, "text": text, "apikey": apikey})
        url = f"{CALLMEBOT_URL}?{params}"
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                body = response.read().decode("utf-8", errors="ignore").strip()
                print(f"[whatsapp] {phone}: {body[:200]}")
        except Exception as exc:
            print(f"[aviso] falha ao enviar WhatsApp para {phone}: {exc}", file=sys.stderr)
        if index < len(recipients) - 1:
            time.sleep(CALLMEBOT_DELAY_BETWEEN_SENDS)


def main():
    urls = load_urls()
    state = load_state()
    now = datetime.now()

    username = os.environ["CONDFY_USERNAME"]
    password = os.environ["CONDFY_PASSWORD"]

    all_pending = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            page.goto(urls[0], wait_until="networkidle", timeout=30000)
            if is_login_form_visible(page):
                do_login(page, username, password)
        except Exception:
            save_debug_artifacts(page, "login_failure")
            raise

        for url in urls:
            try:
                all_pending.extend(collect_pending(page, url))
            except Exception as exc:
                print(f"[erro] falha ao processar {url}: {exc}", file=sys.stderr)
                save_debug_artifacts(page, f"error_{LICENSE_RE.search(url).group(1) if LICENSE_RE.search(url) else 'unknown'}")

        browser.close()

    new_items = [item for item in all_pending if item["key"] not in state]

    if new_items:
        print(f"Encontradas {len(new_items)} nova(s) pendência(s). Enviando e-mail...")
        send_email(new_items)
        send_whatsapp(new_items)
        for item in new_items:
            state[item["key"]] = now.isoformat()
    else:
        print("Nenhuma pendência nova encontrada.")

    state = prune_state(state, now)
    save_state(state)


if __name__ == "__main__":
    main()
