/**
 * FillThatPDF! Version Configuration
 * 
 * This file determines whether the app runs in Standard, PRO, or Demo mode.
 * The build process sets these values automatically.
 */

const config = {
    // Set by build process - DO NOT EDIT MANUALLY
    // For local dev testing we allow overriding to launch PRO features.
    isPro: false,        // true = PRO, false = Standard
    isDemo: false,  // true = Demo (free trial with limits)

    
    // Version info
    version: '1.1.7',
    
    // Demo/Trial limits
    demo: {
        trialDays: 7,           // Number of days for free trial
        showDailyReminder: true // Show daily popup with days remaining
    },
    
    // Feature flags (controlled by isPro and isDemo)
    features: {
        get visualEditor() { return config.isPro; },
        get aiAssistant() { return config.isPro; },
        get bulkProcessing() { return config.isPro; },
        get unlimitedPDFs() { return config.isPro; },
        // Standard features (always available)
        smartDetection: true,
        testFill: true,
        multipleFormats: true,
        lifetimeUpdates: true
    },
    
    // App naming
    get productName() {
        if (config.isDemo) return 'Fill That PDF! (Demo)';
        return config.isPro ? 'Fill That PDF! PRO' : 'Fill That PDF!';
    },
    
    get appId() {
        if (config.isDemo) return 'com.fillthatpdf.demo';
        return config.isPro ? 'com.fillthatpdf.pro' : 'com.fillthatpdf.standard';
    }
};

module.exports = config;

