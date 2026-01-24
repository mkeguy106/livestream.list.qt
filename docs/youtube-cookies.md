# How to Get YouTube Cookies for Chat

To send messages in YouTube live chat, you need to authenticate with your Google account. The app uses browser cookies to authenticate with YouTube's internal API.

## Method 1: Import from Browser (Recommended)

The easiest method — import cookies directly from your installed browser:

1. Make sure you are **logged into YouTube** in your browser (Chrome, Firefox, Brave, etc.)
2. Open **Preferences** > **Accounts**
3. In the YouTube section, click **Import from Browser**
4. Select which browser you are logged into YouTube with
5. Click **OK** — the app reads the cookies directly from the browser's cookie store

That's it! Your YouTube chat sending is now enabled.

> **Supported browsers:** Chrome, Chromium, Brave, Firefox, Opera, Vivaldi, LibreWolf

> **Flatpak users:** Browser import is not available in Flatpak builds because the sandbox cannot access browser data. Use one of the manual methods below instead.

## Method 2: Browser Developer Tools

### Chrome / Chromium / Brave

1. Open **YouTube** in your browser and make sure you're logged in
2. Press `F12` (or `Ctrl+Shift+I`) to open Developer Tools
3. Go to the **Application** tab (or **Storage** in Firefox)
4. In the left sidebar, expand **Cookies** and click on `https://www.youtube.com`
5. Find and copy the values for these cookies:
   - `SID`
   - `HSID`
   - `SSID`
   - `APISID`
   - `SAPISID`
6. Format them as a semicolon-separated string:
   ```
   SID=your_sid_value; HSID=your_hsid_value; SSID=your_ssid_value; APISID=your_apisid_value; SAPISID=your_sapisid_value
   ```
7. Paste this into **Preferences > Accounts > YouTube > Cookies** and click **Save Cookies**

### Firefox

1. Open **YouTube** and ensure you're logged in
2. Press `F12` to open Developer Tools
3. Go to the **Storage** tab
4. Expand **Cookies** in the left sidebar and click `https://www.youtube.com`
5. Use the search/filter box to find each required cookie (SID, HSID, SSID, APISID, SAPISID)
6. Double-click each cookie's **Value** cell to select and copy it
7. Format and paste as described above

## Method 3: Browser Extension

Use a cookie export extension to simplify the process:

- **EditThisCookie** (Chrome)
- **Cookie Quick Manager** (Firefox)

1. Install the extension
2. Navigate to `youtube.com`
3. Open the extension and export cookies
4. Copy only the required cookies (SID, HSID, SSID, APISID, SAPISID) into the format above

## Method 4: curl/wget Header Copy

1. Open YouTube in your browser (logged in)
2. Open Developer Tools (`F12`) > **Network** tab
3. Refresh the page
4. Right-click any request to `youtube.com` and select **Copy as cURL**
5. Find the `-H 'cookie: ...'` portion of the copied command
6. Extract the required cookie values from that string

## Important Notes

- **Security**: These cookies grant access to your YouTube/Google account. Never share them publicly.
- **Expiration**: Cookies typically last 1-2 years unless you change your password or sign out.
- **Scope**: The cookies are only used locally to send chat messages. They are stored in your app's settings file.
- **Revocation**: If you want to revoke access, clear the cookies in the app preferences or change your Google password.
- **Multiple accounts**: The cookies correspond to whichever Google account is active in your browser when you copy them.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Cookies incomplete" warning | Make sure all 5 required cookies are present (SID, HSID, SSID, APISID, SAPISID) |
| Messages fail to send | Cookies may have expired. Re-copy fresh cookies from your browser |
| Wrong account sending | Log into the correct YouTube account in your browser before copying cookies |
| "Send params not available" | The stream may not have live chat enabled, or the chat page structure changed |
