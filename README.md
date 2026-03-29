# Whale Tracker for GitHub + Telegram

Pacote pronto para subir no GitHub e rodar automaticamente de hora em hora com envio de sinais para o Telegram.

## O que este repositório faz

- coleta snapshot das baleias da Hyperliquid
- calcula sinal agregado ponderado
- salva histórico em `data/whale_snapshots.csv`
- gera imagem do snapshot em `data/`
- envia sinal para o Telegram
- executa validação diária do histórico
- persiste tudo no próprio repositório via GitHub Actions

## Estrutura

```text
.
├── .github/workflows/
│   ├── hourly_tracker.yml
│   └── daily_validation.yml
├── api.py
├── backtest.py
├── backtest_runner.py
├── config.py
├── dashboard.py
├── run_once.py
├── whale_signal.py
├── storage.py
├── telegram_notifier.py
├── tracker.py
├── requirements.txt
├── .env.example
├── data/
└── logs/
```

## Segredos do GitHub

No repositório, crie estes secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Como subir

1. Crie um repositório vazio no GitHub.
2. Envie todos os arquivos deste pacote.
3. Em **Settings > Secrets and variables > Actions**, adicione os secrets do Telegram.
4. Em **Actions**, habilite os workflows se necessário.
5. Rode manualmente o workflow `hourly-whale-tracker` uma vez para testar.

## Agendamento

O workflow principal roda todo começo de hora com este cron:

```yaml
- cron: "17 * * * *"
```

Você pode mudar para outro minuto fixo.

## Execução local

```bash
pip install -r requirements.txt
python run_once.py
python tracker.py --once
python tracker.py --interval 4
python backtest_runner.py validate
```

## Variáveis opcionais

Veja `.env.example` para ajustar threshold de sinal, número de baleias e regras de filtro.

## Observações práticas

- o GitHub Actions não é ideal para execução contínua em loop; por isso o modo certo é `run_once.py` com `cron`
- o envio ao Telegram só dispara se houver configuração e, por padrão, apenas para sinais direcionais
- sinais repetidos em janela curta são suprimidos por deduplicação em `data/last_telegram_signal.json`
