import React from 'react';
import CharacterProfilesPanel from '../../CharacterProfilesPanel';

const ProfilesTab = ({ projectName, projectSettings }) => {
    return (
        <div className="space-y-6">
            <CharacterProfilesPanel
                projectName={projectName}
                projectSettings={projectSettings}
            />
        </div>
    );
};

export default ProfilesTab;
