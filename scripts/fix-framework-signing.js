/**
 * afterPack hook — Fix Python.framework signing for PyInstaller onedir bundles
 *
 * PyInstaller copies files into Python.framework instead of preserving symlinks,
 * creating an "ambiguous bundle format". We fix the structure to be a proper
 * Apple framework before electron-builder's signing pass.
 */

const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

function findFrameworks(dir, results = []) {
    if (!fs.existsSync(dir)) return results;
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
        const fullPath = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            if (/^Python3?\.framework$/.test(entry.name)) {
                results.push(fullPath);
            } else if (!entry.name.endsWith('.framework')) {
                findFrameworks(fullPath, results);
            }
        }
    }
    return results;
}

function fixFrameworkStructure(fwPath) {
    const versionsDir = path.join(fwPath, 'Versions');
    if (!fs.existsSync(versionsDir)) return;

    // Determine the binary name from the framework name (Python.framework → Python, Python3.framework → Python3)
    const fwName = path.basename(fwPath).replace('.framework', '');

    // Find the actual version directory (e.g., "3.14", "3.11")
    const versionEntries = fs.readdirSync(versionsDir, { withFileTypes: true })
        .filter(e => e.isDirectory() && e.name !== 'Current');

    if (versionEntries.length === 0) return;

    const actualVersion = versionEntries[0].name;
    const currentLink = path.join(versionsDir, 'Current');

    // Remove the Current directory (it's a copy, should be a symlink)
    if (fs.existsSync(currentLink)) {
        const stat = fs.lstatSync(currentLink);
        if (!stat.isSymbolicLink()) {
            fs.rmSync(currentLink, { recursive: true });
            fs.symlinkSync(actualVersion, currentLink);
        }
    } else {
        fs.symlinkSync(actualVersion, currentLink);
    }

    // Remove top-level binary (should be a symlink into Versions/Current)
    const topBinary = path.join(fwPath, fwName);
    if (fs.existsSync(topBinary)) {
        const stat = fs.lstatSync(topBinary);
        if (!stat.isSymbolicLink()) {
            fs.unlinkSync(topBinary);
            fs.symlinkSync(`Versions/Current/${fwName}`, topBinary);
        }
    }

    // Remove top-level Resources (should be a symlink into Versions/Current)
    const topResources = path.join(fwPath, 'Resources');
    if (fs.existsSync(topResources)) {
        const stat = fs.lstatSync(topResources);
        if (!stat.isSymbolicLink()) {
            fs.rmSync(topResources, { recursive: true });
            fs.symlinkSync('Versions/Current/Resources', topResources);
        }
    }
}

exports.default = async function (context) {
    const { appOutDir, electronPlatformName } = context;
    if (electronPlatformName !== 'darwin') return;

    const identity = 'E3C6B97B885843868879D7360252DE1E1EAF732E';
    const entitlements = path.join(__dirname, '..', 'build', 'entitlements.mac.plist');

    // Find all Python.framework directories inside the app bundle
    const appName = context.packager.appInfo.productFilename;
    const resourcesDir = path.join(appOutDir, `${appName}.app`, 'Contents', 'Resources');
    const frameworks = findFrameworks(resourcesDir);

    if (frameworks.length === 0) {
        console.log('  • No Python.framework found — skipping pre-sign');
        return;
    }

    for (const fw of frameworks) {
        const relPath = path.relative(appOutDir, fw);
        console.log(`  • Fixing framework structure: ${relPath}`);

        // Fix the framework to use proper symlinks
        fixFrameworkStructure(fw);

        // Sign all .dylib and .so files inside the framework
        try {
            const internals = execSync(
                `find "${fw}/Versions" -type f \\( -name "*.dylib" -o -name "*.so" \\)`,
                { encoding: 'utf8' }
            ).trim().split('\n').filter(Boolean);

            for (const lib of internals) {
                execSync(
                    `codesign --sign "${identity}" --force --timestamp --options runtime "${lib}"`,
                    { stdio: 'pipe' }
                );
            }
        } catch (e) {
            // OK if no dylibs found
        }

        // Sign the versioned binary (Python or Python3)
        const fwName = path.basename(fw).replace('.framework', '');
        const versionEntries = fs.readdirSync(path.join(fw, 'Versions'), { withFileTypes: true })
            .filter(e => e.isDirectory() && e.name !== 'Current');

        for (const ver of versionEntries) {
            const verBin = path.join(fw, 'Versions', ver.name, fwName);
            if (fs.existsSync(verBin)) {
                execSync(
                    `codesign --sign "${identity}" --force --timestamp --options runtime --entitlements "${entitlements}" "${verBin}"`,
                    { stdio: 'pipe' }
                );
            }
        }

        // Sign the framework bundle itself
        execSync(
            `codesign --sign "${identity}" --force --timestamp --options runtime --entitlements "${entitlements}" "${fw}"`,
            { stdio: 'pipe' }
        );

        console.log(`  ✅ Fixed & pre-signed: ${relPath}`);
    }
};
