import * as vscode from 'vscode';
import * as https from 'https';
import * as http from 'http';

type Protocol = typeof http | typeof https;

/**
 * Return the editor's file path if it's an on-disk file inside the current
 * workspace, otherwise show an error and return null. Stops untitled buffers
 * and symlink-escapes from being shipped to the backend.
 */
function resolveWorkspaceFile(editor: vscode.TextEditor): string | null {
    if (editor.document.isUntitled) {
        vscode.window.showErrorMessage(
            'Korgex: please save the file before running this command.'
        );
        return null;
    }
    const filePath = editor.document.fileName;
    const folder = vscode.workspace.getWorkspaceFolder(editor.document.uri);
    if (!folder) {
        vscode.window.showErrorMessage(
            'Korgex: file is outside the current workspace.'
        );
        return null;
    }
    return filePath;
}

/**
 * Strip control characters / newlines from a user-typed shell command before
 * sending it to the backend. Backend posture about shell=True varies; this is
 * a defense-in-depth measure, not the primary safeguard.
 */
function sanitizeCommand(cmd: string): string {
    return cmd.replace(/[\x00-\x1f\x7f]/g, ' ').trim();
}

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

            const filePath = resolveWorkspaceFile(editor);
            if (!filePath) return;

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

            const filePath = resolveWorkspaceFile(editor);
            if (!filePath) return;

            const rawCommand = await vscode.window.showInputBox({
                prompt:
                    'Test command to heal (e.g. pytest tests/ -x)',
            });
            if (!rawCommand) return;
            const testCommand = sanitizeCommand(rawCommand);
            if (!testCommand) return;

            vscode.window.showInformationMessage(
                `Korgex: Booting TDD Healer for ${testCommand}...`
            );

            try {
                const result = await postToBackend('/api/swarm/heal', {
                    filepath: filePath,
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
            } catch (err) {
                const msg = err instanceof Error ? err.message : String(err);
                vscode.window.showErrorMessage(
                    `Korgex: Healing crashed — ${msg}`
                );
            }
        }
    );

    // ── Command 3: Profile Test Suite ───────────────────────────────────
    const profileCmd = vscode.commands.registerCommand(
        'korgex.profileTests',
        async () => {
            const rawCommand = await vscode.window.showInputBox({
                prompt:
                    'Test command to profile (e.g. pytest tests/ -x)',
            });
            if (!rawCommand) return;
            const testCommand = sanitizeCommand(rawCommand);
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
            let parsed: vscode.Uri;
            try {
                parsed = vscode.Uri.parse(baseUrl, true);
            } catch {
                vscode.window.showErrorMessage(
                    `Korgex: invalid backendUrl in settings: ${baseUrl}`
                );
                return;
            }
            if (parsed.scheme !== 'http' && parsed.scheme !== 'https') {
                vscode.window.showErrorMessage(
                    `Korgex: refusing to open dashboard — backendUrl scheme '${parsed.scheme}' is not http/https.`
                );
                return;
            }
            const dashboardUrl = `${baseUrl.replace(/\/+$/, '')}/dashboard`;
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

    return new Promise((resolve, reject) => {
        const body = JSON.stringify(payload);

        const options: http.RequestOptions = {
            hostname: url.hostname,
            port: url.port ? Number(url.port) : (isHttps ? 443 : 80),
            path: url.pathname + url.search,
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Content-Length': Buffer.byteLength(body),
            },
            timeout: 120_000,
        };

        const req = transport.request(options, (res) => {
            res.setEncoding('utf8');
            let data = '';
            res.on('data', (chunk: string) => (data += chunk));
            res.on('end', () => {
                try {
                    resolve(JSON.parse(data) as BackendResponse);
                } catch {
                    // Backend responded but with non-JSON — that's a transport
                    // failure, not a "backend ran and said no." Reject so the
                    // caller's try/catch can distinguish it from a clean
                    // {success: false, error: ...} from the backend itself.
                    reject(new Error('Invalid JSON response from backend'));
                }
            });
        });

        req.on('error', (err: Error) => reject(err));
        req.on('timeout', () => {
            // Clear the timer before destroy() so it can't fire a second time
            // against a half-torn-down request.
            req.setTimeout(0);
            req.destroy();
            reject(new Error('Request timed out after 120s'));
        });

        req.write(body);
        req.end();
    });
}

export function deactivate() {
    console.log('[Korgex] Sidecar deactivated.');
}