# Robô de solicitações faciais pendentes (Condfy)

Verifica periodicamente (a cada 20 minutos) as páginas de "Solicitações >
Facial" de 4 licenças no [web.condfy.com.br](https://web.condfy.com.br) e
envia um e-mail sempre que encontra pessoas com status **Pendente** que
ainda não haviam sido notificadas.

## Como funciona

1. `scripts/check_pending.py` usa Playwright (Chromium headless) para fazer
   login no Condfy e visitar cada URL listada em `config/urls.txt`.
2. Para cada página, procura por badges de status "Pendente" e extrai o nome,
   licença, tipo de operação (Inclusão/Exclusão/Alteração) e data/hora da
   solicitação.
3. Compara com `state/notified.json` (pendências já notificadas). Só envia
   e-mail para pendências novas — evita ficar mandando o mesmo e-mail a cada
   20 minutos enquanto a pendência não é resolvida.
4. Se houver algo novo, envia um e-mail via SMTP do Gmail para `NOTIFY_EMAIL`.

## Rodando em servidor/máquina próprio (via cron) — método recomendado

> Este é o método atualmente em uso, pois o GitHub Actions está bloqueado
> para a conta que hospeda este repositório (ver seção "GitHub Actions"
> abaixo). Se isso for resolvido no futuro, o workflow em
> `.github/workflows/check-pending.yml` já está pronto para uso.

### 1. Clonar e instalar dependências

```bash
git clone https://github.com/applycftv-create/Rob-_condfy.git
cd Rob-_condfy
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
```

### 2. Configurar credenciais

```bash
cp .env.example .env
```

Edite o `.env` e preencha com valores reais:

| Variável | Descrição |
|---|---|
| `CONDFY_USERNAME` | Usuário/e-mail de login do Condfy |
| `CONDFY_PASSWORD` | Senha do Condfy |
| `GMAIL_USER` | Conta Gmail que envia a notificação |
| `GMAIL_APP_PASSWORD` | ["Senha de app"](https://myaccount.google.com/apppasswords) do Gmail (requer verificação em duas etapas ativada na conta) |
| `NOTIFY_EMAIL` | Endereço(s) que recebem o alerta — um ou vários, separados por vírgula |

O arquivo `.env` nunca é enviado ao git (está no `.gitignore`).

### 3. Testar manualmente

```bash
source .venv/bin/activate
python scripts/check_pending.py
```

Se der erro de login ou não encontrar as pendências esperadas, confira a
pasta `debug_artifacts/` criada na raiz do projeto — ela contém screenshot e
HTML da página no momento do erro.

### 4. Agendar a cada 20 minutos com cron

```bash
crontab -e
```

Adicione a linha (ajuste o caminho `/caminho/para/Rob-_condfy`):

```cron
*/20 * * * * cd /caminho/para/Rob-_condfy && /caminho/para/Rob-_condfy/.venv/bin/python scripts/check_pending.py >> logs/check_pending.log 2>&1
```

Crie a pasta de logs antes:

```bash
mkdir -p /caminho/para/Rob-_condfy/logs
```

A partir daí o robô roda sozinho a cada 20 minutos, usando o `.env` local
para credenciais e `state/notified.json` para não repetir notificações.

## Editar a lista de URLs monitoradas

Edite `config/urls.txt` — uma URL por linha. Linhas vazias ou começando com
`#` são ignoradas.

## Solução de problemas

O ambiente de desenvolvimento usado para criar este robô não teve acesso à
internet para inspecionar a estrutura real das páginas do Condfy (login
bloqueado por proxy de rede). A extração de pendências foi implementada de
forma resiliente (procura o texto "Pendente" na tela e sobe na árvore do DOM
até achar o bloco com a data), mas pode precisar de ajuste fino no primeiro
uso real.

Se uma execução falhar ou não encontrar pendências que deveriam existir,
verifique a pasta `debug_artifacts/` (screenshot `.png` + HTML `.html` da
página no momento do erro) para identificar se o login falhou ou se a
estrutura da tabela mudou.

## GitHub Actions (alternativa, atualmente indisponível)

Existe um workflow pronto em `.github/workflows/check-pending.yml` que roda
a mesma verificação a cada 20 minutos via GitHub Actions — mas ele não está
funcionando porque a conta GitHub que hospeda este repositório
(`applycftv-create`) está com o Actions bloqueado (não aparece nem na lista
de workflows do repositório, apesar de todas as permissões estarem
corretas). Isso é comum em contas pessoais recém-criadas — o GitHub costuma
liberar após verificação manual pelo suporte
([support.github.com/contact](https://support.github.com/contact)).

Se isso for resolvido, cadastre os secrets `CONDFY_USERNAME`,
`CONDFY_PASSWORD`, `GMAIL_USER`, `GMAIL_APP_PASSWORD` e `NOTIFY_EMAIL` em
**Settings → Secrets and variables → Actions**, com "Workflow permissions"
em "Read and write permissions", e o agendamento passa a valer
automaticamente a partir do branch padrão.
