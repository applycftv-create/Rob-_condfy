# Robô de solicitações faciais pendentes (Condfy)

Verifica periodicamente (a cada 20 minutos, via GitHub Actions) as páginas de
"Solicitações > Facial" de 4 licenças no [web.condfy.com.br](https://web.condfy.com.br)
e envia um e-mail sempre que encontra pessoas com status **Pendente** que
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
5. O workflow do GitHub Actions faz commit do `state/notified.json`
   atualizado de volta no repositório, para persistir entre execuções.

## Configuração necessária

Em **Settings → Secrets and variables → Actions → New repository secret**,
cadastre:

| Secret | Descrição |
|---|---|
| `CONDFY_USERNAME` | Usuário/e-mail de login do Condfy |
| `CONDFY_PASSWORD` | Senha do Condfy |
| `GMAIL_USER` | Conta Gmail que envia a notificação |
| `GMAIL_APP_PASSWORD` | ["Senha de app"](https://myaccount.google.com/apppasswords) do Gmail (não é a senha normal da conta — requer verificação em duas etapas ativada) |
| `NOTIFY_EMAIL` | Endereço(s) que recebem o alerta. Pode ser um único e-mail ou vários, um por linha (ou separados por vírgula/`;`) |

## Editar a lista de URLs monitoradas

Edite `config/urls.txt` — uma URL por linha. Linhas vazias ou começando com
`#` são ignoradas.

## Rodar manualmente

Na aba **Actions** do repositório, escolha o workflow "Verificar solicitações
faciais pendentes (Condfy)" e clique em **Run workflow**.

## Solução de problemas

O ambiente de desenvolvimento usado para criar este robô não teve acesso à
internet para inspecionar a estrutura real das páginas do Condfy (login
bloqueado por proxy de rede). A extração de pendências foi implementada de
forma resiliente (procura o texto "Pendente" na tela e sobe na árvore do DOM
até achar o bloco com a data), mas pode precisar de ajuste fino.

Se uma execução falhar ou não encontrar pendências que deveriam existir:

1. Abra a execução em **Actions**, baixe o artefato `debug-artifacts-<id>`.
2. Ele contém screenshot (`.png`) e HTML (`.html`) da página no momento do
   erro — indicam se o login falhou, se a estrutura da tabela mudou, etc.
3. Compartilhe esses arquivos para ajuste do seletor/script.

## Ativação do agendamento

O gatilho `schedule` do GitHub Actions só é executado a partir do **branch
padrão** do repositório. Depois que este PR for mesclado, o robô passa a
rodar automaticamente a cada 20 minutos.
