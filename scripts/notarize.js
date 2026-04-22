/**
 * Notarization Script for FillThatPDF!
 *
 * Tries methods in priority order:
 *   1. Keychain profile "FillThatPDF" (set up via xcrun notarytool store-credentials)
 *   2. Environment variables: APPLE_ID + APPLE_APP_SPECIFIC_PASSWORD + APPLE_TEAM_ID
 */

const { notarize } = require('@electron/notarize');
const path = require('path');

exports.default = async function notarizing(context) {
    const { electronPlatformName, appOutDir } = context;

    if (electronPlatformName !== 'darwin') {
        console.log('Skipping notarization - not macOS');
        return;
    }

    const appName = context.packager.appInfo.productFilename;
    const appPath = path.join(appOutDir, `${appName}.app`);

    // --- Method 1: keychain profile (preferred — no env vars needed) ---
    const KEYCHAIN_PROFILE = 'FillThatPDF';
    try {
        console.log(`🔐 Notarizing ${appName} via keychain profile "${KEYCHAIN_PROFILE}"...`);
        console.log(`   App: ${appPath}`);
        await notarize({
            tool: 'notarytool',
            appPath,
            keychainProfile: KEYCHAIN_PROFILE,
        });
        console.log('✅ Notarization complete!');
        return;
    } catch (err) {
        console.warn(`⚠️  Keychain-profile notarization failed: ${err.message}`);
        console.log('Falling back to environment variable method...');
    }

    // --- Method 2: environment variables ---
    const applePassword = process.env.APPLE_APP_SPECIFIC_PASSWORD || process.env.APPLE_APP_PASSWORD;
    if (process.env.APPLE_ID && applePassword && process.env.APPLE_TEAM_ID) {
        try {
            console.log(`🔐 Notarizing ${appName} via env vars...`);
            await notarize({
                tool: 'notarytool',
                appPath,
                appleId: process.env.APPLE_ID,
                appleIdPassword: applePassword,
                teamId: process.env.APPLE_TEAM_ID,
            });
            console.log('✅ Notarization complete!');
            return;
        } catch (err) {
            console.error(`⚠️  Env-var notarization failed: ${err.message}`);
        }
    }

    console.log('⚠️  Notarization skipped — no valid credentials found.');
    console.log('   The app is code-signed but not notarized.');
    console.log('   Users may see a Gatekeeper warning on first launch.');
};
