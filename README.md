# 🤖 AutoBet Scanner

**Sports Arbitrage Detection & Execution System**

Scanner automatique d'opportunités d'arbitrage sportif qui compare les cotes de 40+ bookmakers et envoie des alertes en temps réel via Telegram.

## ✨ Fonctionnalités

- 📊 **Scan multi-bookmakers** : Compare les cotes de Pinnacle, Bet365, 1xBet, Betfair, Smarkets, Matchbook, et 30+ autres
- 🎯 **Détection d'arbitrage** : Algorithme optimisé avec prise en compte des commissions d'exchange
- 📱 **Alertes Telegram** : Notifications instantanées avec détails des paris à placer
- 🌐 **Dashboard Web** : Interface de monitoring en temps réel
- ☁️ **Cloudflare Workers** : Déploiement serverless, tourne 24/7 gratuitement
- 💰 **Gestion du risque** : Limites de mise, kill switch, drawdown protection

## 🚀 Démarrage Rapide

### Option 1 : Local (Python)

```bash
# 1. Cloner et installer
git clone https://github.com/you/autobet.git
cd autobet
pip install -e .

# 2. Configurer
cp .env.example .env
# Éditer .env avec vos clés API

# 3. Lancer
autobet run --mode dry --dashboard
```

### Option 2 : Cloudflare Workers (Recommandé - Gratuit 24/7)

```bash
# 1. Installer Wrangler
npm install -g wrangler

# 2. Setup automatique
chmod +x scripts/setup_cloudflare.sh
./scripts/setup_cloudflare.sh

# 3. C'est tout ! Le scanner tourne automatiquement toutes les minutes
```

## 📋 Prérequis

### APIs Requises

| Service | Usage | Free Tier |
|---------|-------|-----------|
| [The Odds API](https://the-odds-api.com/) | Scan des cotes | 500 req/mois |
| [Telegram Bot](https://t.me/BotFather) | Alertes | Illimité |

### Exchanges (Optionnel - pour exécution auto)

| Exchange | Commission | API |
|----------|-----------|-----|
| [Smarkets](https://smarkets.com/) | 2% | Gratuite |
| [Matchbook](https://matchbook.com/) | 2% | Gratuite |
| [Betfair](https://developer.betfair.com/) | 5% | Gratuite |

## 📁 Structure du Projet

```
autobet/
├── src/
│   ├── sources/          # Fetching des cotes (multi-API)
│   │   ├── the_odds_api.py
│   │   └── aggregator.py
│   ├── detector/         # Détection d'arbitrage
│   │   └── arbitrage.py
│   ├── monitoring/       # Telegram + Dashboard
│   │   ├── telegram_bot.py
│   │   └── dashboard.py
│   ├── storage/          # Base de données
│   └── scanner.py        # Orchestrateur principal
├── worker/               # Cloudflare Worker
│   └── main.py
├── config/
│   └── settings.py
└── wrangler.toml         # Config Cloudflare
```

## 🎮 Commandes CLI

```bash
# Lancer le scanner
autobet run --mode dry              # Détection seule (recommandé pour commencer)
autobet run --mode semi-auto        # Alertes + confirmation avant exécution
autobet run --mode auto             # Exécution automatique (⚠️ risqué)

# Options
autobet run --interval 30           # Scan toutes les 30 secondes
autobet run --dashboard             # Activer le dashboard web
autobet run --no-telegram           # Désactiver Telegram

# Autres commandes
autobet scan --once                 # Un seul scan (test)
autobet status                      # Voir l'état actuel
autobet config                      # Voir la configuration
```

## 📱 Commandes Telegram

| Commande | Description |
|----------|-------------|
| `/status` | État du système |
| `/stats` | Statistiques du jour |
| `/opportunities` | Opportunités récentes |
| `/risk` | État de la gestion du risque |
| `/killswitch on/off` | Activer/désactiver le kill switch |
| `/mode dry/semi/auto` | Changer le mode d'exécution |

## 🔧 Configuration

### Variables d'Environnement

```bash
# Obligatoire
ODDS_API_API_KEY=your_key          # The Odds API

# Telegram (recommandé)
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id

# Risk Management
RISK_INITIAL_BANKROLL=1000
RISK_MIN_EDGE_PERCENT=0.8          # Min 0.8% d'edge
RISK_MAX_STAKE_PERCENT=0.02        # Max 2% par pari
RISK_MAX_DAILY_DRAWDOWN_PERCENT=0.05  # Kill switch à -5%
```

## 📊 Comment ça marche ?

### 1. Collecte des cotes
```
The Odds API → 40+ bookmakers → Normalisation → Matrice d'odds
```

### 2. Détection d'arbitrage
```
Pour chaque événement:
  best_home = max(odds_home de tous les bookmakers)
  best_away = max(odds_away de tous les bookmakers)

  implied = 1/best_home + 1/best_away

  Si implied < 1 → ARBITRAGE DÉTECTÉ
  edge = (1 - implied) × 100%
```

### 3. Calcul des mises
```
Pour un profit égal quelle que soit l'issue:
  stake_home = (total × (1/odds_home)) / implied
  stake_away = (total × (1/odds_away)) / implied
```

### 4. Alerte Telegram
```
🤖 AUTO | Edge: 1.52%

📊 Djokovic vs Alcaraz
🏆 ATP Finals

  ✅ smarkets: Djokovic @ 2.10 → 48.50€
  📍 bet365: Alcaraz @ 2.05 → 51.50€

💰 Stake: 100€
💵 Profit: 1.52€
```

## ⚠️ Avertissements

1. **Légalité** : Vérifiez les lois sur les paris sportifs dans votre juridiction
2. **Risques** : L'arbitrage n'est pas sans risque (slippage, comptes limités, erreurs)
3. **Capital** : Ne jouez jamais plus que ce que vous pouvez vous permettre de perdre
4. **Commencez en dry-run** : Validez le système avant de passer en mode auto

## 🤝 Contribution

Les contributions sont bienvenues ! Voir `CONTRIBUTING.md`.

## 📄 License

MIT License - Voir `LICENSE`

---

**Disclaimer** : Ce projet est fourni à titre éducatif. L'auteur n'est pas responsable des pertes financières liées à l'utilisation de ce logiciel.
