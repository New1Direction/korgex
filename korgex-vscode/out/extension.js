"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const https = __importStar(require("https"));
const http = __importStar(require("http"));
function activate(context) {
    console.log('[KorgKode] Sidecar activated.');
    // ── Command 1: Refactor Current File ────────────────────────────────
    const refactorCmd = vscode.commands.registerCommand('korgkode.refactorFile', async () => {
        const editor = vscode.window.activeTextEditor;
        if (!editor) {
            vscode.window.showErrorMessage('KorgKode: No active file to refactor.');
            return;
        }
        const filePath = editor.document.fileName;
        vscode.window.withProgress({
            location: vscode.ProgressLocation.Notification,
            title: `KorgKode analyzing ${filePath}...`,
            cancellable: false,
        }, async (_progress) => {
            const result = await postToBackend('/api/swarm/refactor', {
                filepath: filePath,
            });
            if (result.success) {
                vscode.window.showInformationMessage('KorgKode: Refactoring complete. Check the diff.');
            }
            else {
                vscode.window.showErrorMessage(`KorgKode: Refactoring failed — ${result.error}`);
            }
        });
    });
    // ── Command 2: TDD Healer ───────────────────────────────────────────
    const healCmd = vscode.commands.registerCommand('korgkode.healTests', async () => {
        const editor = vscode.window.activeTextEditor;
        if (!editor) {
            vscode.window.showErrorMessage('KorgKode: No active file.');
            return;
        }
        const testCommand = await vscode.window.showInputBox({
            prompt: 'Test command to heal (e.g. pytest tests/ -x)',
        });
        if (!testCommand)
            return;
        vscode.window.showInformationMessage(`KorgKode: Booting TDD Healer for ${testCommand}...`);
        const result = await postToBackend('/api/swarm/heal', {
            filepath: editor.document.fileName,
            command: testCommand,
        });
        if (result.success) {
            vscode.window.showInformationMessage('KorgKode: Tests healed successfully.');
        }
        else {
            vscode.window.showErrorMessage(`KorgKode: Healing failed — ${result.error}`);
        }
    });
    // ── Command 3: Profile Test Suite ───────────────────────────────────
    const profileCmd = vscode.commands.registerCommand('korgkode.profileTests', async () => {
        const testCommand = await vscode.window.showInputBox({
            prompt: 'Test command to profile (e.g. pytest tests/ -x)',
        });
        if (!testCommand)
            return;
        vscode.window.withProgress({
            location: vscode.ProgressLocation.Notification,
            title: 'KorgKode profiling...',
            cancellable: false,
        }, async (_progress) => {
            const result = await postToBackend('/api/swarm/profile', {
                command: testCommand,
            });
            if (result.success) {
                vscode.window.showInformationMessage('KorgKode: Profiling complete. See Output panel for details.');
            }
            else {
                vscode.window.showErrorMessage(`KorgKode: Profiling failed — ${result.error}`);
            }
        });
    });
    // ── Command 4: Open Swarm Dashboard ─────────────────────────────────
    const dashboardCmd = vscode.commands.registerCommand('korgkode.openDashboard', async () => {
        const config = vscode.workspace.getConfiguration('korgkode');
        const baseUrl = config.get('backendUrl') || 'http://localhost:8000';
        const dashboardUrl = `${baseUrl}/dashboard`;
        vscode.env.openExternal(vscode.Uri.parse(dashboardUrl));
        vscode.window.showInformationMessage(`KorgKode: Dashboard opened at ${dashboardUrl}`);
    });
    context.subscriptions.push(refactorCmd, healCmd, profileCmd, dashboardCmd);
}
function postToBackend(endpoint, payload) {
    const config = vscode.workspace.getConfiguration('korgkode');
    const baseUrl = config.get('backendUrl') || 'http://localhost:8000';
    const url = new URL(endpoint, baseUrl);
    const isHttps = url.protocol === 'https:';
    const transport = isHttps ? https : http;
    return new Promise((resolve) => {
        const body = JSON.stringify(payload);
        const options = {
            hostname: url.hostname,
            port: url.port || (isHttps ? 443 : 80),
            path: url.pathname + url.search,
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Content-Length': Buffer.byteLength(body),
            },
            timeout: 120000,
        };
        const req = transport.request(options, (res) => {
            let data = '';
            res.on('data', (chunk) => (data += chunk));
            res.on('end', () => {
                try {
                    resolve(JSON.parse(data));
                }
                catch {
                    resolve({ success: false, error: 'Invalid JSON response' });
                }
            });
        });
        req.on('error', (err) => resolve({ success: false, error: err.message }));
        req.on('timeout', () => {
            req.destroy();
            resolve({ success: false, error: 'Request timed out' });
        });
        req.write(body);
        req.end();
    });
}
function deactivate() {
    console.log('[KorgKode] Sidecar deactivated.');
}
//# sourceMappingURL=extension.js.map