/**
 * Notarization Script for FillThatPDF!
 * 
 * This script runs after signing to notarize the app with Apple.
 * 
 * Required environment variables:
 *   APPLE_ID         - Your Apple ID email
 *   APPLE_APP_PASSWORD - App-specific password from appleid.apple.com
 *   APPLE_TEAM_ID    - Your Apple Developer Team ID
 */

const { notarize } = require('@electron/notarize');
const path = require('path');

exports.default = async function notarizing(context) {
    const { electronPlatformName, appOutDir } = context;
    
    if (electronPlatformName !== 'darwin') {
        console.log('Skipping notarization - not macOS');
        return;
    }
    
    // Check for required environment variables
    const applePassword = process.env.APPLE_APP_SPECIFIC_PASSWORD || process.env.APPLE_APP_PASSWORD;
    if (!process.env.APPLE_ID || !applePassword || !process.env.APPLE_TEAM_ID) {
        console.log('⚠️  Skipping notarization - environment variables not set');
        console.log('To notarize, set: APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD, APPLE_TEAM_ID');
        return;
    }
    
    const appName = context.packager.appInfo.productFilename;
    const appPath = path.join(appOutDir, `${appName}.app`);
    
    console.log(`🔐 Notarizing ${appName}...`);
    console.log(`   Apple ID: ${process.env.APPLE_ID}`);
    console.log(`   Team ID: ${process.env.APPLE_TEAM_ID}`);
    console.log(`   App Path: ${appPath}`);
    
    try {
        // Use notarytool method (newer, more reliable)
        await notarize({
            tool: 'notarytool',
            appPath: appPath,
            appleId: process.env.APPLE_ID,
            appleIdPassword: applePassword,
            teamId: process.env.APPLE_TEAM_ID,
        });
        console.log('✅ Notarization complete!');
    } catch (error) {
        console.error('⚠️  Notarization failed:', error.message);
        console.log('\nThe app was code signed successfully, but notarization failed.');
        console.log('Possible reasons:');
        console.log('  1. Invalid app-specific password format');
        console.log('  2. Apple Developer account not configured');
        console.log('  3. Network connectivity issues');
        console.log('  4. Apple notarization service temporarily unavailable');
        console.log('\nThe DMG will still be created and signed.');
        console.log('Users may see a warning on first launch - instruct them to:');
        console.log('  Right-click the app → Open → Open\n');
        // Don't throw - allow build to continue with just code signing
    }
};
