import { useSearchParams } from 'react-router-dom';
import { Archive, ArrowDownCircle, HeartPulse, ScrollText } from 'lucide-react';
import { Page, PageHeader, Tabs } from '../ui';
import StatusPanel from './StatusPanel';
import LogsPanel from './LogsPanel';
import BackupPanel from './BackupPanel';
import UpdatesPanel from './UpdatesPanel';

const TABS = [
    { id: 'status', label: 'Status', icon: HeartPulse },
    { id: 'logs', label: 'Logs', icon: ScrollText },
    { id: 'backup', label: 'Backup', icon: Archive },
    { id: 'updates', label: 'Updates', icon: ArrowDownCircle },
];

export default function SystemPage() {
    const [searchParams, setSearchParams] = useSearchParams();
    const requested = searchParams.get('tab');
    const tab = TABS.some(t => t.id === requested) ? requested : 'status';
    const setTab = (id) => setSearchParams(id === 'status' ? {} : { tab: id }, { replace: true });

    return (
        <Page width={tab === 'logs' ? 'wide' : 'narrow'}>
            <PageHeader title="System" subtitle="The server, its logs, backups and updates." />
            <Tabs tabs={TABS} value={tab} onChange={setTab} />
            {tab === 'status' && <StatusPanel />}
            {tab === 'logs' && <LogsPanel />}
            {tab === 'backup' && <BackupPanel />}
            {tab === 'updates' && <UpdatesPanel />}
        </Page>
    );
}
