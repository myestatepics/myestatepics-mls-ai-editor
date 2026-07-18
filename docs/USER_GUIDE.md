# User Guide

## Installation

Open `MyEstatePics AI Editor.dmg`, drag the app to Applications, and eject the
DMG. Unsigned internal builds may require Control-click → Open the first time.

For production, create:

```text
~/Library/Application Support/MyEstatePics AI Editor/.env
```

with one line:

```text
OPENAI_API_KEY=sk-your-key
```

Source launches instead use the repository `.env`.

## Launching

Double-click **MyEstatePics AI Editor**. The header shows application version,
prompt version, API-key status, and Demo status. No Terminal window opens for
the packaged app.

## Folder setup

Choose Incoming and Completed. NeedsReview, Error, and Logs are under Advanced
Folders. Incoming, Completed, NeedsReview, and Error must be distinct, and
result folders must not be inside Incoming. Logs and Data are not part of that
overlap check. The app remembers selections.

Choosing Incoming scans `.jpg`, `.jpeg`, and `.png` files and checks all by
default. Uncheck any image that should not run. Select All, Clear All, Rescan
Folder, and Analyze are secondary controls.

An image is ineligible while the same filename currently exists in the active
Completed, NeedsReview, or Error folder. Delete or move that output to make the
source eligible again.

## Demo Mode

Enable **Demo Mode — No API Charges** to exercise the workflow without a key or
API request. Choose All Pass, Some Need Review, or Include Error. Demo outputs
and records remain under `<repository>/runtime/Demo` for source runs or
Application Support `runtime/Demo` for packaged runs. They do not affect
production routing.

## Production Mode

Disable Demo Mode, choose Low or Medium, review selected count and estimated
cost, and click Start Processing. Confirm the paid summary. Cost is an estimate;
the OpenAI usage dashboard is authoritative.

Cancel stops between images. It cannot cancel an API request already in flight.

## Review Results

The Review window displays Original and AI Output.

- **Accept** moves a NeedsReview output to Completed.
- **Move to Needs Review** routes a Completed output for review.
- **Retry** immediately deletes the current output after confirmation and
  queues the source; it does not start or charge automatically. Use it only
  while the original source remains available.
- **Delete Output** removes only the output.
- Previous and Next navigate results.

## Updating the API key

Open Advanced Folders and select **Open .env**. Save the required line, then
select **Reload API Key**. The application shows only whether a key was loaded.

## Troubleshooting

### OPENAI_API_KEY is missing

Confirm the correct file for the launch mode and remove quotes or spaces around
`=`. Demo Mode remains available.

### No images selected

Click Rescan Folder or Select All. Verify files use a supported extension.

The application does not watch Incoming continuously. Use Rescan Folder or
Analyze after changing files outside the application. Review Results includes
both Completed and NeedsReview outputs.

### Already exists

The status names Completed, NeedsReview, or Error. The existing output prevents
overwrite; historical CSV/SQLite records do not.

### NeedsReview

Open Review Results and read the verifier reason. Do not accept an uncertain
architectural, color, window, or artifact result.

### API failure

Check the activity panel, Error folder, CSV run log, and Application Support
startup log. Do not paste API keys into support messages.
