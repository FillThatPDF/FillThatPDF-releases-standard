/**
 * Notarization Script for FillThatPDF!
 *
 * Tries methods in priority order:
 *   1. Environment variables: NOTARIZE_APPLE_ID + NOTARIZE_APP_PASSWORD + NOTARIZE_TEAM_ID
 *      Uniquely named so electron-builder's auto-detection (which greps for APPLE_ID /
 *      APPLE_APP_SPECIFIC_PASSWORD / APPLE_TEAM_ID) does NOT see them and activate its
 *      buggy built-in notarizer, which crashes with "Cannot destructure property
 *      'appBundleId'". These are safe to keep set globally in ~/.zshrc.
 *   2. Keychain profile "FillThatPDF" — fallback. Requires the login keychain to be
 *      unlocked; auto-lock can break long builds mid-way.
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

    // --- Method 1: environment variables (preferred — no keychain unlock needed) ---
    const envAppleId = process.env.NOTARIZE_APPLE_ID;
    const envPassword = process.env.NOTARIZE_APP_PASSWORD;
    const envTeamId = process.env.NOTARIZE_TEAM_ID;
    if (envAppleId && envPassword && envTeamId) {
        try {
            console.log(`🔐 Notarizing ${appName} via env vars (NOTARIZE_*)...`);
            console.log(`   App: ${appPath}`);
            await notarize({
                tool: 'notarytool',
                appPath,
                appleId: envAppleId,
                appleIdPassword: envPassword,
                teamId: envTeamId,
            });
            console.log('✅ Notarization complete!');
            return;
        } catch (err) {
            console.warn(`⚠️  Env-var notarization failed: ${err.message}`);
            console.log('Falling back to keychain profile...');
        }
    }

    // --- Method 2: keychain profile (fallback) ---
    const KEYCHAIN_PROFILE = 'FillThatPDF';
    try {
        console.log(`🔐 Notarizing ${appName} via keychain profile "${KEYCHAIN_PROFILE}"...`);
        await notarize({
            tool: 'notarytool',
            appPath,
            keychainProfile: KEYCHAIN_PROFILE,
        });
        console.log('✅ Notarization complete!');
        return;
    } catch (err) {
        console.error(`⚠️  Keychain-profile notarization failed: ${err.message}`);
    }

    console.log('⚠️  Notarization skipped — no valid credentials found.');
    console.log('   The app is code-signed but not notarized.');
    console.log('   Users may see a Gatekeeper warning on first launch.');
};
