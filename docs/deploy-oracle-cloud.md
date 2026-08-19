# Deploy no Oracle Cloud Free Tier (grátis para sempre)

Guia passo a passo para colocar o robô rodando 24/7 em uma máquina virtual
gratuita da Oracle Cloud (Always Free — sem prazo de expiração, diferente
dos "trials" de 30 dias de outras nuvens).

## 1. Criar a conta

1. Acesse https://www.oracle.com/cloud/free/ e clique em "Start for free".
2. Preencha os dados. É pedido cartão de crédito só para verificação de
   identidade — não é cobrado nada, a menos que você faça upgrade manual
   para "Pay As You Go" depois.
3. Escolha a **Home Region** com atenção — não pode ser trocada depois.
   Se disponível, prefira uma região mais próxima do Brasil (ex: "Brazil
   East (Sao Paulo)"); caso não esteja disponível para Always Free na sua
   conta, qualquer região da AWS/US funciona tecnicamente igual, só a
   latência de acesso à VM muda um pouco.
4. Contas novas às vezes passam por uma fila de aprovação (pode levar de
   minutos a até 1-2 dias). Aguarde o e-mail de confirmação.

## 2. Criar a máquina virtual (VM)

1. No Console da Oracle Cloud, vá em **Compute → Instances → Create
   Instance**.
2. Nome: `condfy-bot` (ou o que preferir).
3. Em **Image and shape**, clique em "Edit" e escolha:
   - **Image**: Canonical Ubuntu 22.04 (ou 24.04)
   - **Shape**: clique em "Change shape" → aba "Ampere" → escolha
     `VM.Standard.A1.Flex` → configure **1 OCPU / 6 GB de memória**
     (dentro do limite Always Free de até 4 OCPU / 24 GB no total). Essa
     opção tem bem mais memória que a shape x86 "Micro" e roda o Chromium
     do Playwright sem problemas.
4. Em **Add SSH keys**: se você não tem um par de chaves SSH, gere um no
   seu computador:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/condfy-bot -N ""
   ```
   Cole o conteúdo de `~/.ssh/condfy-bot.pub` no campo "SSH public key".
5. Clique em **Create**. Aguarde o status mudar para "Running" (1-2
   minutos) e anote o **Public IP** mostrado na página da instância.

## 3. Conectar via SSH

```bash
ssh -i ~/.ssh/condfy-bot ubuntu@<PUBLIC_IP>
```

(usuário padrão da imagem Ubuntu na Oracle Cloud é `ubuntu`)

## 4. Instalar dependências

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip git
```

## 5. Clonar o repositório e instalar o robô

```bash
git clone https://github.com/applycftv-create/Rob-_condfy.git
cd Rob-_condfy
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
```

## 6. Configurar credenciais

```bash
cp .env.example .env
nano .env
```

Preencha `CONDFY_USERNAME`, `CONDFY_PASSWORD`, `GMAIL_USER`,
`GMAIL_APP_PASSWORD` e `NOTIFY_EMAIL` (veja a tabela no README principal).
Salve com `Ctrl+O`, `Enter`, `Ctrl+X`.

## 7. Testar manualmente

```bash
source .venv/bin/activate
python scripts/check_pending.py
```

Se aparecer erro, verifique a pasta `debug_artifacts/` criada na raiz do
projeto (screenshot + HTML da página no momento do erro) para diagnosticar
se foi falha de login ou mudança na estrutura da página.

## 8. Agendar a cada 20 minutos com cron

```bash
mkdir -p ~/Rob-_condfy/logs
crontab -e
```

Adicione a linha (ajuste `/home/ubuntu` se seu usuário for diferente):

```cron
*/20 * * * * cd /home/ubuntu/Rob-_condfy && /home/ubuntu/Rob-_condfy/.venv/bin/python scripts/check_pending.py >> /home/ubuntu/Rob-_condfy/logs/check_pending.log 2>&1
```

Salve e saia. A partir daí o robô roda sozinho a cada 20 minutos, mesmo se
você desconectar o SSH — a Oracle Cloud mantém a VM ligada continuamente.

## 9. Verificar se está funcionando

```bash
tail -f ~/Rob-_condfy/logs/check_pending.log
```

Espere até 20 minutos e veja se uma nova linha de execução aparece no log.

## Dicas de manutenção

- Para atualizar o código depois de mudanças no repositório:
  ```bash
  cd ~/Rob-_condfy && git pull && source .venv/bin/activate && pip install -r requirements.txt
  ```
- Para editar a lista de URLs monitoradas: `nano ~/Rob-_condfy/config/urls.txt`
- Para pausar temporariamente: `crontab -e` e comente a linha com `#` na frente.
