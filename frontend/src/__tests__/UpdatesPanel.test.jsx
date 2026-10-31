import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// The Updates panel with an update that waits for the work in progress: the panel says what it waits for
// and offers Cancel, and Install pressed during work answers "deferred" instead of failing. API mocked.

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../api', () => ({
    api: {
        getUpdateStatus: vi.fn(),
        checkForUpdates: vi.fn(),
        applyUpdate: vi.fn(),
        cancelPendingUpdate: vi.fn(),
        getSystemStatus: vi.fn(() => Promise.resolve({ data: { started_at: 1 } })),
        setAutoUpdateConfig: vi.fn(() => Promise.resolve({ data: {} })),
    },
}));

import { api } from '../api';
import UpdatesPanel from '../components/system/UpdatesPanel';

const pendingUpdate = (over = {}) => ({
    state: 'holding', source: 'GitHub push abcdef1', commit: 'abcdef1', requested_at: '2026-10-31T12:00:00+00:00',
    holding: true, holding_since: '2026-10-31T12:10:00+00:00', hold_at: null, hold_lifted: false,
    busy: ['Harbour Lights S01E02 (agy runner)'], attempts: 0, next_try: null, last_result: null, cancellable: true,
    message: 'Waiting for the work in hand to finish (Harbour Lights S01E02 (agy runner)). No new work starts until the update is installed and OmbiSub has restarted.',
    ...over,
});

const status = (over = {}) => ({
    checked: true, channel: 'main', update_available: true, behind: 2, ahead: 0,
    commits: [{ hash: 'abcdef1', message: 'New things' }], current_commit: '1111111', current_commit_full: '1111111aaaa',
    latest_commit: 'abcdef1', installed_release: { tag_name: 'untagged' }, auto_update_enabled: true,
    auto_update_interval_hours: 6, update_channel: 'auto', pending_update: null, last_deferred_update: null,
    ...over,
});

beforeEach(() => {
    vi.clearAllMocks();
});

describe('UpdatesPanel and a waiting update', () => {
    it('shows what the update waits for, and Cancel lifts it', async () => {
        api.getUpdateStatus.mockResolvedValueOnce({ data: status({ pending_update: pendingUpdate() }) })
            .mockResolvedValue({ data: status() });
        api.cancelPendingUpdate.mockResolvedValue({ data: { cancelled: true, message: 'The waiting update was cancelled; new work goes on as usual.' } });
        render(<UpdatesPanel />);

        expect(await screen.findByText(/Update waiting: no new work starts/)).toBeInTheDocument();
        expect(screen.getByText(/Harbour Lights S01E02 \(agy runner\)/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
        await waitFor(() => expect(api.cancelPendingUpdate).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(screen.queryByText(/Update waiting/)).not.toBeInTheDocument());
        expect(toast.info).toHaveBeenCalledWith('The waiting update was cancelled; new work goes on as usual.');
    });

    it('offers no Cancel once the install has begun', async () => {
        api.getUpdateStatus.mockResolvedValue({ data: status({ pending_update: pendingUpdate({ state: 'installing', cancellable: false, message: 'Installing the update now.' }) }) });
        render(<UpdatesPanel />);
        expect(await screen.findByText(/Installing the update \(/)).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
    });

    it('Install pressed during work shows the update as waiting instead of an error', async () => {
        // The first status has nothing waiting; once Install was pressed the server reports the waiting update.
        api.getUpdateStatus.mockResolvedValueOnce({ data: status() })
            .mockResolvedValue({ data: status({ pending_update: pendingUpdate({ source: 'Install button' }) }) });
        api.applyUpdate.mockResolvedValue({ data: { status: 'deferred', message: 'Waiting for the work in hand to finish.', pending_update: pendingUpdate({ source: 'Install button' }) } });
        render(<UpdatesPanel />);
        fireEvent.click(await screen.findByRole('button', { name: /Install and restart/ }));
        const dialog = await screen.findByRole('dialog', { name: 'Install the update?' });
        expect(within(dialog).getByText(/no new work starts and the update goes in once they are finished/)).toBeInTheDocument();
        fireEvent.click(within(dialog).getByRole('button', { name: 'Install and restart' }));
        await waitFor(() => expect(api.applyUpdate).toHaveBeenCalledWith(false));
        expect(await screen.findByText(/Update waiting: no new work starts \(Install button/)).toBeInTheDocument();
        expect(toast.info).toHaveBeenCalledWith('Waiting for the work in hand to finish.');
        expect(toast.error).not.toHaveBeenCalled();
    });

    it('says why the last waiting update was not installed', async () => {
        api.getUpdateStatus.mockResolvedValue({ data: status({ last_deferred_update: {
            status: 'local_changes', message: '2 file(s) have uncommitted changes that an update would erase.',
            source: 'GitHub push abcdef1', requested_at: '2026-10-31T12:00:00+00:00', ended_at: '2026-10-31T12:20:00+00:00' } }) });
        render(<UpdatesPanel />);
        expect(await screen.findByText(/was not installed/)).toBeInTheDocument();
        expect(screen.getByText(/uncommitted changes/)).toBeInTheDocument();
    });
});
