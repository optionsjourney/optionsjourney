# Installing OptionsJourney

Download the latest version from the [Releases page](https://github.com/optionsjourney/optionsjourney/releases/latest): under **Assets**, click **Source code (zip)**.

## Windows
1. Open your **Downloads** folder, right-click the zip and choose **Extract All…**, then **Extract**.
   - Unzip it first. Double-clicking the zip only *shows* what's inside, and the installer can't run from there.
2. In the folder that opens, open **scripts** and double-click **Install OptionsJourney.bat**.
   - If Windows says "Windows protected your PC", click **More info**, then **Run anyway**.
3. A black window shows the progress. The first install downloads Python and takes a minute or two.
4. When it's done, an **OJ** icon appears in the system tray (click **^** at the bottom right if you don't see it), and the app opens in your browser. The [setup guide](setup.md) takes it from there.

The app is installed in your user folder (`AppData\Local\Programs\OptionsJourney`), so you can delete the zip and the extracted folder afterwards. To start it again later, choose **OptionsJourney** in the **Start menu**.

## Mac
1. Open your **Downloads** folder and double-click the zip to unzip it.
2. Open the new folder, then **scripts**, and double-click **Install OptionsJourney.command**.
   - The first time, macOS may say it can't be opened because it's from an unidentified developer. Right-click the file, choose **Open**, then **Open** again.
3. A Terminal window shows the progress. The first install downloads Python and takes a minute or two.
4. When it's done, an **OJ** icon appears in the menu bar and the app opens in your browser. The [setup guide](setup.md) takes it from there.

The app is installed in `Applications/OptionsJourney` inside your home folder, so you can delete the zip and the unzipped folder afterwards. To start it again later, open **OptionsJourney** from Spotlight (⌘ Space) or Launchpad.

## Where things are
- **The app:** the folder above. **Show app in Finder / Explorer** in the OJ menu opens it.
- **Your data** (trades, bots, settings, backups) is kept separately, so updating or reinstalling the app never touches it: `AppData\Local\OptionsJourney` on Windows, `Library/Application Support/OptionsJourney` on a Mac.
- **Logs:** a `logs` folder inside your data. **Open logs** in the OJ menu opens it.
- **Broker logins** are kept in the computer's own password store (Credential Manager on Windows, Keychain on a Mac).
