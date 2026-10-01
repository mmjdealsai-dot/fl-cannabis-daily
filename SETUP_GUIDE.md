# Setup guide: Florida Medical Cannabis Daily

No coding needed. Everything happens in your web browser, and it takes about 45 minutes the first time. Once it's set up, the agent runs by itself every morning around 6 AM Eastern, emails you the digest, and updates your website.

You'll set up three free or low-cost accounts:

- **A new Gmail account** for the bot. It sends you the daily email and (optionally) collects dispensary promo emails.
- **An Anthropic API account**, which is how the agent uses Claude to research. This is the only part that costs money, billed by usage.
- **A GitHub account**, which runs the agent every morning and hosts the website for free.

---

## Step 1. Create the bot's Gmail account

1. Create a new Gmail account just for this, for example `flcannabisdaily.bot@gmail.com`. Keeping it separate from your personal and practice email is safer and keeps dispensary promo emails out of your real inbox.
2. In that new account, go to **myaccount.google.com → Security** and turn on **2-Step Verification**. (Google requires this before the next step.)
3. Still in Security, search for **App passwords** (or go to myaccount.google.com/apppasswords). Create one named `digest bot`.
4. Google shows a 16-letter password. **Copy it into a note right away.** You won't be able to see it again.

## Step 2. Get your Anthropic API key

1. Go to **console.anthropic.com** and sign up.
2. Under **Billing**, add a payment method and some prepaid credit (starting with $20 is plenty for testing).
3. Under **Limits**, set a **monthly spend limit** (for example $75). This guarantees the bill can never run away.
4. Under **API Keys**, click **Create Key**, name it `digest bot`, and copy the key (it starts with `sk-ant-`). Save it in your note.

**What it costs:** each morning Claude runs a few dozen web searches and reads the results. My rough estimate is $1 to $4 per day, but check the **Usage** page after the first few runs to see your real number. If you want it cheaper, see "Lower the cost" at the bottom.

## Step 3. Put the project on GitHub

1. Sign up at **github.com** (free).
2. Click the **+** in the top right → **New repository**.
   - Name: `fl-cannabis-daily`
   - Choose **Public** (free website hosting requires a public repository; your passwords stay private no matter what, see Step 4).
   - Check **Add a README file**.
   - Click **Create repository**.
3. Unzip the `fl-cannabis-daily.zip` file I gave you on your computer.
4. In your new repository, click **Add file → Upload files**. Drag in these items from the unzipped folder: `agent.py`, `config.yaml`, `requirements.txt`, `site_template.html`, `SETUP_GUIDE.md`, and the **docs** and **data** folders. Click **Commit changes**.
5. **Important:** there's a hidden folder called `.github` that most computers won't let you drag. Create that file by hand instead:
   - Click **Add file → Create new file**.
   - In the name box, type exactly: `.github/workflows/daily-digest.yml` (the slashes create the folders automatically).
   - Open `daily-digest.yml` from the unzipped folder in Notepad (Windows) or TextEdit (Mac), copy everything, and paste it into the big box on GitHub. If you can't see the file, open the `daily-digest.yml` copy I included next to the zip.
   - Click **Commit changes**.

## Step 4. Add your passwords as "secrets"

Secrets are stored encrypted by GitHub. They never appear in your files or on your website, even in a public repository.

In your repository, go to **Settings → Secrets and variables → Actions → New repository secret**. Add these four, one at a time (the name must match exactly, in capitals):

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your key from Step 2 (starts with `sk-ant-`) |
| `GMAIL_ADDRESS` | the bot Gmail address from Step 1 |
| `GMAIL_APP_PASSWORD` | the 16-letter app password from Step 1 |
| `DIGEST_TO` | where you want the digest sent, e.g. your personal email. For several addresses, separate them with commas. |

## Step 5. Let the agent save its work

1. Go to **Settings → Actions → General**.
2. Scroll to **Workflow permissions**, choose **Read and write permissions**, and click **Save**.

## Step 6. Turn on the website

1. Go to **Settings → Pages**.
2. Under **Source**, choose **Deploy from a branch**.
3. Under **Branch**, choose **main** and the **/docs** folder. Click **Save**.
4. After a minute or two, refresh the page. GitHub shows your site's address, something like `https://yourname.github.io/fl-cannabis-daily/`. Until the first run, it says the first update arrives tomorrow morning.
5. Copy that address. Open `config.yaml` in your repository, click the **pencil icon** to edit, paste the address between the quotes on the `url:` line, and click **Commit changes**. Now your daily email will link to the site.

## Step 7. Run it once to test

1. Click the **Actions** tab. If GitHub asks, click the green button to enable workflows.
2. Click **Daily Florida cannabis digest** on the left, then **Run workflow → Run workflow**.
3. A run appears with a yellow dot. It takes roughly 10 to 25 minutes. Click it to watch the progress messages if you're curious.
4. A green check means success. Check your email (look in spam the first time and mark it **Not spam**), then refresh your website.

From now on it runs by itself every morning. GitHub sometimes starts scheduled runs 10 to 30 minutes late; that's normal.

---

## Optional: collect deals from dispensary promo emails

Many dispensaries send their best daily deals by email instead of posting them online. To capture those:

1. Sign up for each dispensary's email list using the **bot Gmail address**.
2. In your repository, edit `config.yaml`. Under `inbox:`, change `enabled: false` to `enabled: true`, and commit.

The agent will then read the last day's promo emails each morning and add those deals too.

## Changing things later

Everything you'd normally want to change is in `config.yaml`, which you edit on GitHub with the pencil icon:

- **Add or remove dispensaries:** edit the list at the bottom. Keep the `  - ` at the start of each line.
- **Change the website's title, description, or disclaimer:** edit the `site:` section.
- **Change the run time:** edit the `cron:` line in `.github/workflows/daily-digest.yml`. The time is in UTC, which is 4 hours ahead of Eastern in summer and 5 in winter. `"15 10 * * *"` means 10:15 UTC.

## Lower the cost

In `config.yaml`, you can change `model:` to `"claude-haiku-4-5-20251001"`, which is much cheaper and still good at this kind of task, or reduce the `searches_per_dispensary` number from 4 to 2 or 3.

## If something goes wrong

Open the **Actions** tab and click the failed run (red X), then the **digest** job, to see the messages. Common causes:

- **"Missing ANTHROPIC_API_KEY"**: a secret name is misspelled in Step 4, or it wasn't saved.
- **"Username and Password not accepted"**: use the 16-letter **app password**, not your normal Gmail password, and make sure 2-Step Verification is on.
- **"credit balance is too low"**: add credit in the Anthropic console.
- **An error mentioning `web_search` or tool type**: in `config.yaml`, change `web_search_tool:` to `"web_search_20250305"`.
- **Error at "Save today's results" mentioning permission**: redo Step 5.
- **Some dispensaries listed as "Couldn't check"** in the email: that's usually a temporary hiccup and resolves the next day.

You can paste any error message into Claude and ask what to do.

## Before you share the site publicly

The site is set up to be informational only. Because Florida law places limits on qualified physicians' financial relationships with and advertising connected to MMTCs, it's worth having a healthcare attorney take a quick look before you promote it to patients. Keep it free of affiliate links, sponsored placements, or anything paid for by a dispensary.
