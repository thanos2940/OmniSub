import React from 'react';
import EpisodeSummariesPanel from '../../EpisodeSummariesPanel';

const SummariesTab = ({ projectName, projectSettings }) => {
    return (
        <div className="space-y-6">
            <EpisodeSummariesPanel
                projectName={projectName}
                projectSettings={projectSettings}
            />
        </div>
    );
};

export default SummariesTab;
