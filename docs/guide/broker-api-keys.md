# Getting your broker API keys

OptionsJourney talks to your broker through the broker's API, the same way other trading apps do. Before you connect a broker in the app, you create your own API "application" on the broker's website. That gives you keys only you hold, and they're stored only on your computer.

**Start with Schwab early.** Schwab checks every new app by hand, which takes a few business days. TastyTrade's keys work straight away.

You need only the broker or brokers you trade with. The app also works with neither, if you add your trades by hand.

## TastyTrade
1. Log in at **my.tastytrade.com**.
2. Open **Manage → My Profile → API → OAuth Applications**, then click **+ New OAuth client**.
3. Fill in the form:
   - **Client Name:** leave it as it is.
   - **Redirect URI:** type `https://127.0.0.1`. It isn't used, but the form needs a full address.
   - **Scopes:** tick **read**, **trade** and **openid**.
4. Click create. Copy the **Client Secret** somewhere safe straight away, because TastyTrade shows it only once. If you lose it, **Regenerate** makes a new one, but that also stops any refresh token you already made.
5. Back on **OAuth Applications**, click **Manage** next to your application, then **Create Grant**. Copy the **refresh token** it shows.
6. In OptionsJourney (setup, or **Settings → Brokers**), paste the client secret and the refresh token, then click **Connect TastyTrade**. The app checks them with TastyTrade before keeping them.

The refresh token doesn't run out. If you think someone else has seen it, delete the grant on the same API page and create a new one.

**A sandbox (test) account** has its own keys, made the same way on TastyTrade's sandbox site. Tick **This is a sandbox (test) account** when you paste them. Sandbox keys don't work on your real account, and real keys don't work on the sandbox.

## Schwab
1. Go to **developer.schwab.com** and create a free developer account. It's separate from your Schwab brokerage login.
2. If Schwab asks what you're building, choose access for an **individual** trader, and link it to your Schwab brokerage account when asked.
3. Open **Dashboard → Apps → Create App** and fill in the form:
   - **API products:** pick both **Accounts and Trading Production** and **Market Data Production**.
   - **App name:** anything, for example "OptionsJourney".
   - **Callback URL:** `https://127.0.0.1` exactly.
4. Submit. The app's status shows as waiting for approval. Schwab usually approves it within a few business days, and the status changes to **Ready For Use**.
5. Once it's ready, open the app and copy its **App Key** and **Secret**.
6. In OptionsJourney (setup, or **Settings → Brokers**), paste them, keep the callback URL as `https://127.0.0.1`, and click **Log in to Schwab**.
7. Log in to Schwab and tick the accounts you want the app to see. Schwab then sends you to a page at 127.0.0.1 that shows an error. That's expected. Copy that page's full address from your browser's address bar and paste it into OptionsJourney.

Schwab asks you to log in again every 7 days. The app reminds you a day before (see [Schwab: one computer at a time](setup.md#schwab-one-computer-at-a-time)).

## Keeping your keys safe
- Your keys give access to your account, including placing orders. Don't share them, email them or paste them anywhere other than OptionsJourney.
- OptionsJourney keeps them in your computer's own password store (Credential Manager on Windows, Keychain on a Mac). They're never sent anywhere except to your broker.
- To stop the app using them, click **Disconnect** in **Settings → Brokers**. You can also delete the application on the broker's website.
