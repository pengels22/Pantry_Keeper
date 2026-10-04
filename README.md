# Pantry Keeper iOS

The `ios-app` branch contains the SwiftUI app and a matching backend source copy.
The Recipes tab connects to your Pantry Keeper server for AI recipe suggestions,
usable ingredient measurements, saved recipes, and cooking sessions.

## Build in Xcode

1. Pull the latest `ios-app` branch and open `Pantry Keeper.xcodeproj`.
2. Select your signing team and an iPhone or simulator, then build and run.
3. In Settings, enter the server's reachable URL, for example
   `http://192.168.1.6:8000` if that is your server's address. `localhost` on an
   iPhone means the phone itself. Allow local network access when prompted.
4. Open Recipes, configure usable quantity/unit for ingredients, then request a meal.
5. Select a suggestion, tap Start Cooking to reserve ingredients, review actual
   usage (zero for unused items), and confirm Finished Cooking to deduct stock.

Saved sessions can be reopened after relaunch. Cancel releases reservations without
consuming stock. Closing details does not cancel a session. Requests that change
stock are not automatically retried after a lost response; current state is reloaded.

## Backend

Use your updated existing server, or run `Backend/Pantry_Keeper` following its README.
Set `OPENAI_API_KEY` in that backend's `.env` and restart it. The API key stays on the
server; no OpenAI secret belongs in the iOS app. The copied backend includes the
Recipe Assistant routes, migration script, web UI, and tests. Use the migration
script before deploying to an existing database; it creates a SQLite backup.

The app needs `/api/inventory`, `/api/recipes/chat`, and `/api/recipes/*` session routes.
Recipe generation checks live stock; older inventory counts require explicit usable
measurements. Weight and volume cannot be converted into each other automatically.

## Validation

Backend: run the Python unit tests and `npm test` as described in its README.
Swift request/response contracts: run `bash Tests/run-recipe-contracts.sh` on a Mac
with Xcode command line tools (or a system with Swift installed).
The existing project targets iOS 26.5; use Xcode with that SDK or later.
A full iOS build and device test require Xcode and the iOS SDK. The Linux development
server validated Swift syntax and backend tests; it cannot run the Swift contract
executable or build the iOS target.
