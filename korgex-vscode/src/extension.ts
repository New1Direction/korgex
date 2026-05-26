import * as vscode from 'vscode';
import * as https from 'https';
import * as http from 'http';

type Protocol = typeof http | typeof https;

export function activate(context: vscode.ExtensionContext) {
    console.log('[Korgex] Sidecar activated.');

    // ── Command 1: Refactor Current File ────────────────────────────────
    const refactorCmd = vscode.commands.registerCommand(
        'korgex.refactorFile',
        async () => {
            const editor = vscode.window.activeTextEditor;
            if (!editor) {
                vscode.window.showErrorMessage(
                    'Korgex: No active file to refactor.'
                );
                return;
            }

            const filePath = editor.document.fileName;

            vscode.window.withProgress(
                {
                    location: vscode.ProgressLocation.Notification,
                    title: `Korgex analyzing ${filePath}...`,
                    cancellable: false,
                },
                async (_progress) => {
                    try {
                        const result = await postToBackend(
                            '/api/swarm/refactor',
                            { filepath: filePath }
                        );
                        if (result.success) {
                            vscode.window.showInformationMessage(
                                'Korgex: Refactoring complete. Check the diff.'
                            );
                        } else {
                            vscode.window.showErrorMessage(
                                `Korgex: Refactoring failed — ${result.error}`
                            );
                        }
                    } catch (err) {
                        const msg =
                            err instanceof Error ? err.message : String(err);
                        vscode.window.showErrorMessage(
                            `Korgex: Refactoring crashed — ${msg}`
                        );
                    }
                }
            );
        }
    );

    // ── Command 2: TDD Healer ───────────────────────────────────────────
    const healCmd = vscode.commands.registerCommand(
        'korgex.healTests',
        async () => {
            const editor = vscode.window.activeTextEditor;
            if (!editor) {
                vscode.window.showErrorMessage(
                    'Korgex: No active file.'
                );
                return;
            }

            const testCommand = await vscode.window.showInputBox({
                prompt:
                    'Test command to heal (e.g. pytest tests/ -x)',
            });
            if (!testCommand) return;

            vscode.window.showInformationMessage(
                `Korgex: Booting TDD Healer for ${testCommand}...`
            );

            const result = await postToBackend('/api/swarm/heal', {
                filepath: editor.document.fileName,
                command: testCommand,
            });
            if (result.success) {
                vscode.window.showInformationMessage(
                    'Korgex: Tests healed successfully.'
                );
            } else {
                vscode.window.showErrorMessage(
                    `Korgex: Healing failed — ${result.error}`
                );
            }
        }
    );

    // ── Command 3: Profile Test Suite ───────────────────────────────────
    const profileCmd = vscode.commands.registerCommand(
        'korgex.profileTests',
        async () => {
            const testCommand = await vscode.window.showInputBox({
                prompt:
                    'Test command to profile (e.g. pytest tests/ -x)',
            });
            if (!testCommand) return;

            vscode.window.withProgress(
                {
                    location: vscode.ProgressLocation.Notification,
                    title: 'Korgex profiling...',
                    cancellable: false,
                },
                async (_progress) => {
                    try {
                        const result = await postToBackend(
                            '/api/swarm/profile',
                            { command: testCommand }
                        );
                        if (result.success) {
                            vscode.window.showInformationMessage(
                                'Korgex: Profiling complete. See Output panel for details.'
                            );
                        } else {
                            vscode.window.showErrorMessage(
                                `Korgex: Profiling failed — ${result.error}`
                            );
                        }
                    } catch (err) {
                        const msg =
                            err instanceof Error ? err.message : String(err);
                        vscode.window.showErrorMessage(
                            `Korgex: Profiling crashed — ${msg}`
                        );
                    }
                }
            );
        }
    );

    // ── Command 4: Open Swarm Dashboard ─────────────────────────────────
    const dashboardCmd = vscode.commands.registerCommand(
        'korgex.openDashboard',
        async () => {
            const config = vscode.workspace.getConfiguration('korgex');
            const baseUrl =
                config.get<string>('backendUrl') || 'http://localhost:8090';
            const dashboardUrl = `${baseUrl}/dashboard`;
            vscode.env.openExternal(vscode.Uri.parse(dashboardUrl));
            vscode.window.showInformationMessage(
                `Korgex: Dashboard opened at ${dashboardUrl}`
            );
        }
    );

    context.subscriptions.push(
        refactorCmd,
        healCmd,
        profileCmd,
        dashboardCmd
    );
}

// ── Backend Comms ──────────────────────────────────────────────────────

interface BackendResponse {
    success: boolean;
    error?: string;
    [key: string]: unknown;
}

function postToBackend(
    endpoint: string,
    payload: Record<string, unknown>
): Promise<BackendResponse> {
    const config = vscode.workspace.getConfiguration('korgex');
    const baseUrl: string =
        config.get<string>('backendUrl') || 'http://localhost:8090';

    const url = new URL(endpoint, baseUrl);
    const isHttps = url.protocol === 'https:';
    const transport: Protocol = isHttps ? https : http;

    return new Promise((resolve) => {
        const body = JSON.stringify(payload);

        const options: http.RequestOptions = {
            hostname: url.hostname,
            port: url.port || (isHttps ? 443 : 80),
            path: url.pathname + url.search,
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Content-Length': Buffer.byteLength(body),
            },
            timeout: 120_000,
        };

        const req = transport.request(options, (res) => {
            let data = '';
            res.on('data', (chunk: string) => (data += chunk));
            res.on('end', () => {
                try {
                    resolve(JSON.parse(data) as BackendResponse);
                } catch {
                    resolve({ success: false, error: 'Invalid JSON response' });
                }
            });
        });

        req.on('error', (err: Error) =>
            resolve({ success: false, error: err.message })
        );
        req.on('timeout', () => {
            req.destroy();
            resolve({ success: false, error: 'Request timed out' });
        });

        req.write(body);
        req.end();
    });
}

export function deactivate() {
    console.log('[Korgex] Sidecar deactivated.');
}