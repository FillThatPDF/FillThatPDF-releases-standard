#!/usr/bin/env node
/**
 * Build script for FillThatPDF!
 * Sets the version type before building.
 *
 * Usage:
 *   node build.js standard            - Build Standard version (macOS DMG)
 *   node build.js pro                 - Build PRO version (macOS DMG)
 *   node build.js demo                - Build Demo version (macOS DMG)
 *   node build.js pro --platform win  - Build PRO version (Windows NSIS installer)
 *   node build.js standard --platform win - Build Standard (Windows NSIS installer)
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// Parse arguments
const args = process.argv.slice(2);
const version = args.find(a => !a.startsWith('--')) || 'pro';
const platformIdx = args.indexOf('--platform');
const targetPlatform = platformIdx !== -1 && args[platformIdx + 1] ? args[platformIdx + 1] : (process.platform === 'win32' ? 'win' : 'mac');
const isWindows = targetPlatform === 'win';

const isDemo = version.toLowerCase() === 'demo';
const isPro = version.toLowerCase() === 'pro' || isDemo; // Demo gets PRO features

const versionLabel = isDemo ? 'Demo' : (isPro ? 'PRO' : 'Standard');
const platformLabel = isWindows ? 'Windows' : 'macOS';
// Sanitized name for file artifacts (no spaces or special chars)
const artifactBaseName = isDemo ? 'FillThatPDF-Demo' : (isPro ? 'FillThatPDF-PRO' : 'FillThatPDF');
console.log(`\n🔧 Building FillThatPDF! ${versionLabel} version for ${platformLabel}...\n`);

// Update config.js with correct values
const configPath = path.join(__dirname, 'config.js');
let configContent = fs.readFileSync(configPath, 'utf8');
configContent = configContent.replace(
    /isPro:\s*(true|false)/,
    `isPro: ${isPro}`
);
configContent = configContent.replace(
    /isDemo:\s*(true|false)/,
    `isDemo: ${isDemo}`
);
// Sync version from package.json so the in-app About dialog never drifts
// from the actual shipped version. Added in v1.2.2 — previously the
// `version` string in config.js was hand-edited and got stale (was '1.1.9'
// on a v1.2.1 build). package.json is the single source of truth.
const pkgVersion = JSON.parse(fs.readFileSync(path.join(__dirname, 'package.json'), 'utf8')).version;
configContent = configContent.replace(
    /version:\s*'[^']*'/,
    `version: '${pkgVersion}'`
);
fs.writeFileSync(configPath, configContent);
console.log(`✅ Set isPro = ${isPro}`);
console.log(`✅ Set isDemo = ${isDemo}`);
console.log(`✅ Set version = ${pkgVersion}`);

// Update package.json productName and appId
const packagePath = path.join(__dirname, 'package.json');
const originalPackageJson = fs.readFileSync(packagePath, 'utf8');
const pkg = JSON.parse(originalPackageJson);

if (isDemo) {
    pkg.productName = 'Fill That PDF! (Demo)';
    pkg.build.appId = 'com.fillthatpdf.demo';
    pkg.build.productName = 'Fill That PDF! (Demo)';
} else if (isPro) {
    pkg.productName = 'Fill That PDF! PRO';
    pkg.build.appId = 'com.fillthatpdf.pro';
    pkg.build.productName = 'Fill That PDF! PRO';
} else {
    pkg.productName = 'Fill That PDF!';
    pkg.build.appId = 'com.fillthatpdf.standard';
    pkg.build.productName = 'Fill That PDF!';
}

// Set publish repo based on version type
if (isPro) {
    pkg.build.publish.repo = 'FillThatPDF-releases-pro';
} else {
    pkg.build.publish.repo = 'FillThatPDF-releases-standard';
}

// Filter extraResources to only include platform-appropriate dist folders
// This prevents electron-builder from failing when dist folders for other platforms don't exist
if (isWindows) {
    // On Windows build: remove dist_arm64 and dist_x64 (macOS only)
    pkg.build.extraResources = pkg.build.extraResources.filter(r => {
        const from = typeof r === 'string' ? r : r.from;
        return !from.includes('dist_arm64') && !from.includes('dist_x64');
    });
    // Use sanitized artifact name for Windows installer (no spaces or special chars)
    pkg.build.nsis = pkg.build.nsis || {};
    pkg.build.nsis.artifactName = `${artifactBaseName}-\${version}-Setup.\${ext}`;
    console.log('✅ Filtered extraResources for Windows (removed macOS dist folders)');
} else {
    // On macOS build: remove dist_win (Windows only)
    pkg.build.extraResources = pkg.build.extraResources.filter(r => {
        const from = typeof r === 'string' ? r : r.from;
        return !from.includes('dist_win');
    });
    console.log('✅ Filtered extraResources for macOS (removed Windows dist folder)');
}

fs.writeFileSync(packagePath, JSON.stringify(pkg, null, 2) + '\n');
console.log(`✅ Updated package.json for ${versionLabel} (${platformLabel})`);
console.log(`✅ Publish repo: ${pkg.build.publish.repo}`);

// Build per-architecture to avoid universal merge issues with PyInstaller binaries
function buildForArch(arch) {
    const pkg2 = JSON.parse(originalPackageJson);
    const otherArch = arch === 'arm64' ? 'x64' : 'arm64';
    const otherDistFolder = `dist_${otherArch}`;

    // Apply the same version/product name changes
    if (isDemo) {
        pkg2.productName = 'Fill That PDF! (Demo)';
        pkg2.build.appId = 'com.fillthatpdf.demo';
        pkg2.build.productName = 'Fill That PDF! (Demo)';
    } else if (isPro) {
        pkg2.productName = 'Fill That PDF! PRO';
        pkg2.build.appId = 'com.fillthatpdf.pro';
        pkg2.build.productName = 'Fill That PDF! PRO';
    } else {
        pkg2.productName = 'Fill That PDF!';
        pkg2.build.appId = 'com.fillthatpdf.standard';
        pkg2.build.productName = 'Fill That PDF!';
    }

    if (isPro) {
        pkg2.build.publish.repo = 'FillThatPDF-releases-pro';
    } else {
        pkg2.build.publish.repo = 'FillThatPDF-releases-standard';
    }

    // Only include the Python binaries for this architecture
    pkg2.build.extraResources = pkg2.build.extraResources.filter(r => {
        const from = typeof r === 'string' ? r : r.from;
        return !from.includes(otherDistFolder) && !from.includes('dist_win');
    });

    // Use sanitized artifact name and DMG title (no special chars for hdiutil)
    const safeArtifactName = `${artifactBaseName}-\${version}-\${arch}.\${ext}`;
    pkg2.build.mac.artifactName = safeArtifactName;
    pkg2.build.dmg.artifactName = safeArtifactName;
    pkg2.build.dmg.title = artifactBaseName;

    fs.writeFileSync(packagePath, JSON.stringify(pkg2, null, 2) + '\n');
    console.log(`\n📦 Building ${arch} DMG...\n`);
    execSync(`npx electron-builder --mac dmg zip --${arch}`, { stdio: 'inherit' });
    console.log(`\n✅ ${arch} build complete!\n`);
}

// Run electron-builder
console.log('\n📦 Running electron-builder...\n');
try {
    if (isWindows) {
        execSync('npx electron-builder --win nsis --x64', { stdio: 'inherit' });
        console.log(`\n🎉 Build complete! Check the dist/ folder for your ${versionLabel} Windows installer.\n`);
    } else {
        // Build both architectures separately
        buildForArch('arm64');
        buildForArch('x64');
        console.log(`\n🎉 Both builds complete! Check the dist/ folder for your ${versionLabel} DMGs.\n`);
    }
} catch (error) {
    console.error('❌ Build failed:', error.message);
    // Restore original package.json before exiting
    fs.writeFileSync(packagePath, originalPackageJson);
    process.exit(1);
} finally {
    // Always restore original package.json so filtered extraResources don't persist
    fs.writeFileSync(packagePath, originalPackageJson);
    console.log('✅ Restored original package.json');
}
