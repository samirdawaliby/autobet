#!/bin/bash
# Setup script for Cloudflare Workers deployment

set -e

echo "🚀 AutoBet Cloudflare Setup"
echo "=========================="

# Check if wrangler is installed
if ! command -v wrangler &> /dev/null; then
    echo "📦 Installing Wrangler CLI..."
    npm install -g wrangler
fi

# Login to Cloudflare
echo ""
echo "🔐 Logging into Cloudflare..."
wrangler login

# Create KV namespace
echo ""
echo "📦 Creating KV namespace..."
KV_RESULT=$(wrangler kv:namespace create "AUTOBET_KV" 2>&1)
echo "$KV_RESULT"

# Extract KV ID
KV_ID=$(echo "$KV_RESULT" | grep -o 'id = "[^"]*"' | cut -d'"' -f2)
if [ -n "$KV_ID" ]; then
    echo "✅ KV Namespace ID: $KV_ID"
    # Update wrangler.toml
    sed -i.bak "s/YOUR_KV_NAMESPACE_ID/$KV_ID/" wrangler.toml
fi

# Create D1 database
echo ""
echo "🗄️ Creating D1 database..."
D1_RESULT=$(wrangler d1 create autobet 2>&1)
echo "$D1_RESULT"

# Extract D1 ID
D1_ID=$(echo "$D1_RESULT" | grep -o 'database_id = "[^"]*"' | cut -d'"' -f2)
if [ -n "$D1_ID" ]; then
    echo "✅ D1 Database ID: $D1_ID"
    # Update wrangler.toml
    sed -i.bak "s/YOUR_D1_DATABASE_ID/$D1_ID/" wrangler.toml
fi

# Set secrets
echo ""
echo "🔑 Setting up secrets..."
echo "Please enter your API keys when prompted:"

echo ""
read -p "Enter your The Odds API key: " ODDS_API_KEY
if [ -n "$ODDS_API_KEY" ]; then
    echo "$ODDS_API_KEY" | wrangler secret put ODDS_API_KEY
fi

echo ""
read -p "Enter your Telegram Bot Token (or press Enter to skip): " TELEGRAM_TOKEN
if [ -n "$TELEGRAM_TOKEN" ]; then
    echo "$TELEGRAM_TOKEN" | wrangler secret put TELEGRAM_BOT_TOKEN
fi

echo ""
read -p "Enter your Telegram Chat ID (or press Enter to skip): " TELEGRAM_CHAT
if [ -n "$TELEGRAM_CHAT" ]; then
    echo "$TELEGRAM_CHAT" | wrangler secret put TELEGRAM_CHAT_ID
fi

# Deploy
echo ""
echo "🚀 Deploying worker..."
wrangler deploy

echo ""
echo "✅ Setup complete!"
echo ""
echo "Your worker is now deployed and will scan for arbitrage opportunities every minute."
echo ""
echo "Useful commands:"
echo "  wrangler tail              # View live logs"
echo "  wrangler dev               # Local development"
echo "  curl YOUR_WORKER_URL/scan  # Trigger manual scan"
echo "  curl YOUR_WORKER_URL/stats # View statistics"
