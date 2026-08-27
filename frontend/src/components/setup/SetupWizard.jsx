import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X } from 'lucide-react';
import { api } from '../../api';
import StepWelcome from './StepWelcome';
import StepProvider from './StepProvider';
import StepSecurity from './StepSecurity';
import StepArr from './StepArr';
import StepPathCheck from './StepPathCheck';
import StepQuality from './StepQuality';
import StepFinish from './StepFinish';

// First-run setup wizard (docs/PLAN_onboarding_wizard.md). Every step writes
// through the same endpoints Settings itself uses — this is an orchestration
// layer, not a parallel persistence path. Fresh installs are forced through
// it once (App.jsx gates on GET /api/setup/status); existing installs never
// see it unless the user re-opens it from Settings.
//
// The wizard as a whole is always closeable (X / "skip setup") — closing it
// just leaves auth unconfigured, which surfaces as a persistent warning
// elsewhere (TopBar, Settings) rather than a hard lockout. The one place that
// can't be skipped is the Security step's own inline skip button while no
// credentials exist yet — see the onSkip computation below.
const SetupWizard = ({ onFinished }) => {
    const [stepIndex, setStepIndex] = useState(0);
    const [authRequired, setAuthRequired] = useState(false);
    const [arrConfiguredThisRun, setArrConfiguredThisRun] = useState(false);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        // Whether step 3 (Security) can be skipped depends on whether auth was
        // already configured before the wizard opened (re-run case) — a truly
        // fresh install can't skip it.
        api.getSetupStatus()
            .then(res => setAuthRequired(!res.data.auth_configured))
            .catch(() => setAuthRequired(false))
            .finally(() => setLoading(false));
    }, []);

    const steps = [
        { key: 'welcome', Component: StepWelcome },
        { key: 'provider', Component: StepProvider },
        { key: 'security', Component: StepSecurity },
        { key: 'arr', Component: StepArr },
        { key: 'path', Component: StepPathCheck },
        { key: 'quality', Component: StepQuality },
        { key: 'finish', Component: StepFinish },
    ].filter(s => s.key !== 'path' || arrConfiguredThisRun); // path check only makes sense if arr was touched

    const goNext = () => setStepIndex(i => Math.min(i + 1, steps.length - 1));
    const goBack = () => setStepIndex(i => Math.max(i - 1, 0));

    const finish = async () => {
        try {
            await api.updateSettings({ setup_completed: true });
        } catch (e) {
            console.error('Failed to mark setup complete', e);
        }
        onFinished?.();
    };

    const handleSkipAll = async () => {
        try {
            await api.updateSettings({ setup_completed: true });
        } catch (e) {
            console.error('Failed to mark setup complete', e);
        }
        onFinished?.();
    };

    if (loading) return null;

    const { Component } = steps[stepIndex];
    const isSecurityStep = steps[stepIndex].key === 'security';
    const isLast = stepIndex === steps.length - 1;

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-50 dark:bg-gray-900 p-4">
            <motion.div
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                className="w-full max-w-xl bg-white dark:bg-gray-800 rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]"
            >
                <div className="px-6 pt-5 pb-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                        {steps.map((s, i) => (
                            <div
                                key={s.key}
                                className={`h-1.5 rounded-full transition-all ${i === stepIndex ? 'w-6 bg-indigo-600' : i < stepIndex ? 'w-1.5 bg-indigo-300' : 'w-1.5 bg-gray-200 dark:bg-gray-600'}`}
                            />
                        ))}
                    </div>
                    <button onClick={handleSkipAll} className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300" title="Close setup">
                        <X size={18} />
                    </button>
                </div>

                <div className="p-6 overflow-y-auto flex-1">
                    <AnimatePresence mode="wait">
                        <motion.div
                            key={steps[stepIndex].key}
                            initial={{ opacity: 0, x: 12 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: -12 }}
                            transition={{ duration: 0.15 }}
                        >
                            <Component
                                onNext={goNext}
                                onBack={stepIndex > 0 ? goBack : null}
                                onSkip={!(isSecurityStep && authRequired) ? goNext : null}
                                onFinish={finish}
                                isLast={isLast}
                                setArrConfiguredThisRun={setArrConfiguredThisRun}
                            />
                        </motion.div>
                    </AnimatePresence>
                </div>

                {stepIndex === 0 && (
                    <div className="px-6 pb-5 text-center">
                        <button onClick={handleSkipAll} className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 underline">
                            Skip setup — I'll configure things myself
                        </button>
                    </div>
                )}
            </motion.div>
        </div>
    );
};

export default SetupWizard;
