// Queue statuses that belong on the Queue tab; everything else (completed, failed, cancelled) is History.
export const QUEUE_STATUSES = ['pending', 'queued', 'running', 'paused_daily_limit', 'postponed'];

export const isQueueStatus = (status) => QUEUE_STATUSES.includes(status);
