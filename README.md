# ⚡ TradeAI Mobile — NSE Futures & Options Analysis Engine

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Android Ready](https://img.shields.io/badge/Android-APK%20Included-green.svg)](TradeAI.apk)

**TradeAI** is a real-time NSE Futures & Options analysis, market structure scanner, and paper-trading dashboard built with Python (Flask + WebSocket) and native Android mobile integration.

---

## 🌟 Highlights & Features

- **Strategy F (NIFTY 5m Sustained Breakout + F&O OI Confluence)**:
  - 5-minute candlestick validation with EMA 10, EMA 21, EMA 50, Supertrend (7, 3), and Anchored VWAP (AVWAP).
  - Volume expansion filter (>=1.5x 20-candle moving average).
  - Futures Open Interest buildup tracking (Long Buildup, Short Buildup, Short Covering, Long Unwinding).
  - Options chain ATM/ITM/OTM Put vs Call writing and PCR confluence.
- **Strategy E (Smart Money Scanner)**:
  - Bottom Accumulation & Top Distribution pattern recognition across the entire F&O universe (~187 symbols).
- **Argon Modern Cockpit**:
  - Live charts powered by TradingView Lightweight-Charts.
  - Real-time LTP streaming and instant voice synthesis alerts.
- **Dual Runtime Support**:
  - **Cloud Ready**: 1-click deployment on [Render](https://dashboard.render.com/) with HTTPS and dynamic `$PORT`.
  - **Mobile Ready**: Native standalone Android APK ([TradeAI.apk](TradeAI.apk)) for on-the-go monitoring.

---

## 🚀 1-Click Deployment on Render

Deploy TradeAI directly to [Render](https://dashboard.render.com/) with zero configuration:

1. Link your GitHub repository [`TradeAI_Mobile`](https://github.com/primeinvestorpro-ship-it/TradeAI_Mobile) in Render Dashboard.
2. Select **Web Service** or use the included [`render.yaml`](render.yaml) Blueprint.
3. In Render Environment Variables, configure:
   | Key | Description | Example |
   |---|---|---|
   | `KITE_API_KEY` | Zerodha Kite Connect API Key | `your_api_key` |
   | `KITE_API_SECRET` | Zerodha Kite Connect API Secret | `your_api_secret` |
   | `GEMINI_API_KEY` | Google Gemini API Key for AI note generation | `your_gemini_key` |
   | `DRY_RUN` | Paper trading mode (`true` or `false`) | `false` |
4. In your [Zerodha Kite Developer Console](https://developers.kite.trade/apps), set your Redirect URL to:
   ```text
   https://<your-render-subdomain>.onrender.com/kite/callback
   ```
5. Click **Deploy**. Your cloud dashboard will be live at `https://<your-render-subdomain>.onrender.com`.

---

## 📱 Android App Setup

TradeAI includes a pre-compiled, signed Android APK: [`TradeAI.apk`](TradeAI.apk).

### Quick Install
1. Download [`TradeAI.apk`](TradeAI.apk) directly or navigate to:
   ```text
   https://<your-render-subdomain>.onrender.com/static/TradeAI.apk
   ```
2. Install the APK on any Android 8.0+ device.
3. Tap **⚙ IP** in the top bar of the Android app:
   - Select **☁ Render** and enter your Render URL (e.g. `https://tradeai-mobile.onrender.com`).
   - Or select **💻 Wi-Fi** if running locally on your PC (e.g. `http://192.168.1.43:5050`).
4. TradeAI will connect directly to your trading session.

---

## 💻 Running Locally

```bash
# 1. Clone the repository
git clone https://github.com/primeinvestorpro-ship-it/TradeAI_Mobile.git
cd TradeAI_Mobile

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your configuration from the template
cp settings.example.json settings.json
# Edit settings.json with your API credentials

# 4. Start the engine
python app.py
```
Open [http://127.0.0.1:5050](http://127.0.0.1:5050) in your browser.

---

## 🧪 Testing

Run the full automated test suite (143 tests):
```bash
pytest . -q --tb=short
```

---

## 🔒 Security Notice
- Never commit your `token.json` or `settings.json` containing live broker secrets or API keys.
- Both files are pre-configured in `.gitignore`. Use environment variables when hosting on cloud providers like Render.

---

## ⚖️ Disclaimer
For educational and simulated paper-trading research purposes only. Not financial advice. Always adhere to SEBI guidelines and exchange regulations.
