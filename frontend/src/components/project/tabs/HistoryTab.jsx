import React from 'react';
import TMStatsPanel from '../../TMStatsPanel';
import ReviewQueuePanel from '../../ReviewQueuePanel';

const HistoryTab = ({ projectName }) => {
    return (
        <div className="space-y-6">
            <TMStatsPanel projectName={projectName} />
            <ReviewQueuePanel projectName={projectName} />
        </div>
    );
};

export default HistoryTab;
