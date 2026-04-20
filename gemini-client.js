/**
 * Gemini AI Client for FillThatPDF
 * Provides context-aware AI assistance for PDF field operations
 * Supports function calling for field modifications
 */

const { GoogleGenerativeAI } = require('@google/generative-ai');

// Define available functions for the AI to call
const FIELD_FUNCTIONS = {
    renameField: {
        name: 'renameField',
        description: 'Rename a PDF form field to a new name',
        parameters: {
            type: 'object',
            properties: {
                fieldName: {
                    type: 'string',
                    description: 'Current name of the field to rename'
                },
                newName: {
                    type: 'string', 
                    description: 'New name for the field'
                }
            },
            required: ['fieldName', 'newName']
        }
    },
    renameSelectedFields: {
        name: 'renameSelectedFields',
        description: 'Rename all currently selected fields with a pattern or specific name',
        parameters: {
            type: 'object',
            properties: {
                newName: {
                    type: 'string',
                    description: 'New name for the field(s). Use {n} for auto-numbering, e.g., "Address_{n}"'
                }
            },
            required: ['newName']
        }
    },
    setFieldProperty: {
        name: 'setFieldProperty',
        description: 'Set a property on selected fields',
        parameters: {
            type: 'object',
            properties: {
                property: {
                    type: 'string',
                    enum: ['fontSize', 'textAlign', 'required', 'readOnly', 'fontColor', 'fillColor', 'tooltip'],
                    description: 'The property to change'
                },
                value: {
                    type: 'string',
                    description: 'The new value for the property'
                }
            },
            required: ['property', 'value']
        }
    },
    selectFields: {
        name: 'selectFields',
        description: 'Select fields in the PDF based on criteria. Can select all fields, by name pattern, by type, or fields on a specific page.',
        parameters: {
            type: 'object',
            properties: {
                mode: {
                    type: 'string',
                    enum: ['all', 'pattern', 'type', 'page'],
                    description: 'Selection mode: all=all fields, pattern=by name pattern, type=by field type, page=by page number'
                },
                pattern: {
                    type: 'string',
                    description: 'For pattern mode: name pattern to match (supports * wildcard, e.g., "Table_A_*" or "*Address*"). Case-insensitive.'
                },
                fieldType: {
                    type: 'string',
                    enum: ['text', 'checkbox', 'radio', 'dropdown', 'signature', 'button'],
                    description: 'For type mode: the field type to select'
                },
                pageNumber: {
                    type: 'number',
                    description: 'For page mode: the page number (1-indexed) to select fields from'
                }
            },
            required: ['mode']
        }
    },
    setPropertyOnFilteredFields: {
        name: 'setPropertyOnFilteredFields',
        description: 'Set a property on multiple fields matching a filter, without needing to select them first. Perfect for bulk changes like "set all text fields to 12pt font".',
        parameters: {
            type: 'object',
            properties: {
                filter: {
                    type: 'string',
                    enum: ['all', 'pattern', 'type', 'selected'],
                    description: 'Filter mode: all=all fields, pattern=by name pattern, type=by field type, selected=currently selected'
                },
                pattern: {
                    type: 'string',
                    description: 'For pattern filter: name pattern (supports * wildcard, e.g., "Invoice_*")'
                },
                fieldType: {
                    type: 'string',
                    enum: ['text', 'checkbox', 'radio', 'dropdown', 'signature', 'button'],
                    description: 'For type filter: the field type to match'
                },
                property: {
                    type: 'string',
                    enum: ['fontSize', 'textAlign', 'required', 'readOnly', 'fontColor', 'fillColor', 'tooltip'],
                    description: 'The property to change'
                },
                value: {
                    type: 'string',
                    description: 'The new value for the property'
                }
            },
            required: ['filter', 'property', 'value']
        }
    },
    deleteFields: {
        name: 'deleteFields',
        description: 'Delete the currently selected fields',
        parameters: {
            type: 'object',
            properties: {
                confirm: {
                    type: 'boolean',
                    description: 'Must be true to proceed with deletion'
                }
            },
            required: ['confirm']
        }
    },
    autoNameSelected: {
        name: 'autoNameSelected',
        description: 'Automatically name the selected fields based on nearby text in the PDF',
        parameters: {
            type: 'object',
            properties: {}
        }
    }
};

class GeminiClient {
    constructor(apiKey) {
        this.apiKey = apiKey;
        this.genAI = null;
        this.model = null;
        this.chatSession = null;
        
        if (apiKey) {
            this.initialize(apiKey);
        }
    }
    
    initialize(apiKey) {
        this.apiKey = apiKey;
        this.genAI = new GoogleGenerativeAI(apiKey);
        this.resolvedModelName = null;  // Will be set by _resolveLatestFlashModel
        this.chatSession = null;
    }

    /**
     * Auto-detect the latest available Gemini Flash model via the models.list API.
     * Prefers: gemini-3-flash > gemini-2.5-flash > gemini-2.0-flash
     * Falls back to hardcoded default if the API call fails.
     */
    async _resolveLatestFlashModel() {
        const FALLBACK_MODEL = 'gemini-2.5-flash';
        try {
            const res = await fetch(
                `https://generativelanguage.googleapis.com/v1beta/models?key=${this.apiKey}`
            );
            if (!res.ok) return FALLBACK_MODEL;
            const data = await res.json();
            const models = (data.models || [])
                .map(m => m.name.replace('models/', ''))
                .filter(n =>
                    n.includes('flash') &&
                    !n.includes('preview') &&
                    !n.includes('lite') &&
                    !n.includes('thinking') &&
                    !n.includes('exp') &&
                    n.match(/^gemini-\d/)
                );
            // Sort descending by version number so newest is first
            models.sort((a, b) => {
                const va = a.match(/gemini-(\d+)\.?(\d*)/);
                const vb = b.match(/gemini-(\d+)\.?(\d*)/);
                if (!va || !vb) return 0;
                const majA = parseInt(va[1]), minA = parseInt(va[2] || '0');
                const majB = parseInt(vb[1]), minB = parseInt(vb[2] || '0');
                return majB !== majA ? majB - majA : minB - minA;
            });
            console.log('[GeminiClient] Available flash models:', models);
            const best = models[0] || FALLBACK_MODEL;
            console.log('[GeminiClient] Auto-selected model:', best);
            return best;
        } catch (err) {
            console.warn('[GeminiClient] Could not fetch model list, using fallback:', err.message);
            return FALLBACK_MODEL;
        }
    }

    /**
     * Ensure the model is resolved and ready. Called lazily before first use.
     */
    async _ensureModel() {
        if (this.model) return;
        if (!this.resolvedModelName) {
            this.resolvedModelName = await this._resolveLatestFlashModel();
        }
        const tools = [{
            functionDeclarations: Object.values(FIELD_FUNCTIONS)
        }];
        this.model = this.genAI.getGenerativeModel({
            model: this.resolvedModelName,
            generationConfig: {
                temperature: 0.7,
                topP: 0.95,
                topK: 40,
                maxOutputTokens: 2048,
            },
            tools: tools
        });
    }

    async testApiKey() {
        // Resolve the latest model, then test it
        if (!this.resolvedModelName) {
            this.resolvedModelName = await this._resolveLatestFlashModel();
        }
        const testModel = this.genAI.getGenerativeModel({ model: this.resolvedModelName });
        const result = await testModel.generateContent('Say OK');
        return result.response.text();
    }
    
    
    /**
     * Start a new chat session with PDF context
     */
    async startChat(pdfContext) {
        await this._ensureModel();
        const systemInstruction = this._buildSystemPrompt(pdfContext);

        this.chatSession = this.model.startChat({
            history: [
                {
                    role: 'user',
                    parts: [{ text: `Context about the current PDF:\n${systemInstruction}\n\nPlease acknowledge you understand the context and are ready to help. You can also make changes to fields when asked.` }]
                },
                {
                    role: 'model', 
                    parts: [{ text: 'I understand! I can see the PDF field information you\'ve provided. I\'m ready to help you with:\n\n- **Answering questions** about fields and features\n- **Making changes** to fields (renaming, properties, etc.)\n- **Troubleshooting** issues\n- **Suggestions** for improving your form\n\nWhat would you like me to help with?' }]
                }
            ]
        });
        
        return this.chatSession;
    }
    
    /**
     * Send a message and get a response
     * Returns either text response or function call action
     */
    async sendMessage(message, pdfContext = null) {
        await this._ensureModel();

        // If no active session or context changed, start new chat
        if (!this.chatSession) {
            await this.startChat(pdfContext);
        }
        
        try {
            const result = await this.chatSession.sendMessage(message);
            const response = await result.response;
            
            // Check if AI wants to call a function
            const candidate = response.candidates?.[0];
            if (candidate?.content?.parts) {
                for (const part of candidate.content.parts) {
                    if (part.functionCall) {
                        // AI wants to perform an action
                        return {
                            type: 'action',
                            action: part.functionCall.name,
                            params: part.functionCall.args,
                            message: this._describeAction(part.functionCall.name, part.functionCall.args)
                        };
                    }
                }
            }
            
            // Regular text response
            return {
                type: 'text',
                message: response.text()
            };
        } catch (error) {
            console.error('Gemini API error:', error);
            throw new Error(`AI Error: ${error.message}`);
        }
    }
    
    /**
     * Confirm action was executed and continue conversation
     */
    async confirmAction(action, params, success, resultMessage) {
        if (!this.chatSession) return;
        
        try {
            // Send function response back to continue the conversation
            const functionResponse = {
                functionResponse: {
                    name: action,
                    response: {
                        success: success,
                        message: resultMessage
                    }
                }
            };
            
            const result = await this.chatSession.sendMessage([functionResponse]);
            const response = await result.response;
            return response.text();
        } catch (error) {
            console.error('Error confirming action:', error);
            return success ? 'Done!' : 'There was an issue applying the change.';
        }
    }
    
    /**
     * Generate a human-readable description of the action
     */
    _describeAction(actionName, params) {
        switch (actionName) {
            case 'renameField':
                return `Rename field "${params.fieldName}" to "${params.newName}"`;
            case 'renameSelectedFields':
                return `Rename selected field(s) to "${params.newName}"`;
            case 'setFieldProperty':
                return `Set ${params.property} to "${params.value}" on selected field(s)`;
            case 'selectFields':
                if (params.mode === 'all') return `Select all fields`;
                if (params.mode === 'pattern') return `Select fields matching pattern "${params.pattern}"`;
                if (params.mode === 'type') return `Select all ${params.fieldType} fields`;
                if (params.mode === 'page') return `Select all fields on page ${params.pageNumber}`;
                return `Select fields`;
            case 'setPropertyOnFilteredFields':
                let filterDesc = params.filter;
                if (params.filter === 'all') filterDesc = 'all fields';
                else if (params.filter === 'pattern') filterDesc = `fields matching "${params.pattern}"`;
                else if (params.filter === 'type') filterDesc = `all ${params.fieldType} fields`;
                else if (params.filter === 'selected') filterDesc = 'selected fields';
                return `Set ${params.property} to "${params.value}" on ${filterDesc}`;
            case 'deleteFields':
                return `Delete the selected field(s)`;
            case 'autoNameSelected':
                return `Auto-name the selected field(s) based on nearby text`;
            default:
                return `Perform action: ${actionName}`;
        }
    }
    
    /**
     * Build system prompt with PDF context
     */
    _buildSystemPrompt(context) {
        let prompt = `You are an AI assistant for "Fill That PDF!", an application that creates and edits fillable PDF forms.

Your role is to help users:
- Troubleshoot issues with their PDF fields
- Explain how to use the editor features
- Suggest improvements for their forms
- Answer questions about PDF form best practices
- **Make changes to fields when requested** using the available functions

When the user asks you to make changes (rename, modify properties, delete), use the appropriate function.
Be concise, helpful, and practical. Use markdown formatting for clarity.

**Available Actions:**
- renameField: Rename a specific field by name
- renameSelectedFields: Rename all currently selected fields
- setFieldProperty: Change properties like fontSize, textAlign, required, readOnly, fontColor, fillColor, tooltip
- selectFields: Select fields by pattern (e.g., "Table_*"), by type (all checkboxes), by page, or select ALL fields
- setPropertyOnFilteredFields: Apply property changes to filtered fields WITHOUT needing to select them first (e.g., "set all text fields to 15pt font")
- deleteFields: Delete selected fields (requires confirmation)
- autoNameSelected: Auto-name selected fields based on nearby PDF text

**Important:** When asked to change properties on multiple fields, use setPropertyOnFilteredFields with the appropriate filter. For example:
- "Change all text fields to 15pt" → setPropertyOnFilteredFields with filter="type", fieldType="text", property="fontSize", value="15"
- "Set font size to 12 on fields matching Invoice_*" → setPropertyOnFilteredFields with filter="pattern", pattern="Invoice_*", property="fontSize", value="12"`;

        if (context) {
            prompt += `\n\n## Current PDF Information\n`;
            
            if (context.pdfPath) {
                prompt += `- **File**: ${context.pdfPath.split('/').pop()}\n`;
            }
            
            if (context.totalFields) {
                prompt += `- **Total Fields**: ${context.totalFields}\n`;
            }
            
            if (context.fieldTypes) {
                prompt += `- **Field Types**: ${JSON.stringify(context.fieldTypes)}\n`;
            }
            
            if (context.selectedFields && context.selectedFields.length > 0) {
                prompt += `\n### Currently Selected Fields:\n`;
                context.selectedFields.forEach(f => {
                    prompt += `- **${f.name}** (${f.type})`;
                    if (f.rect) prompt += ` at position [${f.rect.map(Math.round).join(', ')}]`;
                    prompt += `\n`;
                });
            } else {
                prompt += `\n_No fields currently selected._\n`;
            }
            
            if (context.fields && context.fields.length > 0) {
                prompt += `\n### All Fields in PDF:\n`;
                const fieldList = context.fields.slice(0, 50).map(f => `${f.name} (${f.type})`);
                prompt += fieldList.join(', ');
                if (context.fields.length > 50) {
                    prompt += `\n... and ${context.fields.length - 50} more fields`;
                }
            }
            
            if (context.lastError) {
                prompt += `\n\n### Recent Error:\n\`\`\`\n${context.lastError}\n\`\`\``;
            }
        }
        
        return prompt;
    }
    
    /**
     * Reset chat session (for new conversation)
     */
    resetChat() {
        this.chatSession = null;
    }
    
    /**
     * Check if API is configured
     */
    isConfigured() {
        return !!this.apiKey && !!this.model;
    }
}

module.exports = { GeminiClient, FIELD_FUNCTIONS };
