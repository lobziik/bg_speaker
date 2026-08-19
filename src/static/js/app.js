/**
 * Narrator Web UI - WebSocket Integration
 */

class NarratorUI {
    constructor() {
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
        this.reconnectDelay = 1000;
        this.statusElement = document.getElementById('ws-status');

        this.connectWebSocket();
    }

    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/overlay`;

        this.updateStatus('connecting');

        try {
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                console.log('[NarratorUI] WebSocket connected');
                this.reconnectAttempts = 0;
                this.reconnectDelay = 1000;
                this.updateStatus('connected');
            };

            this.ws.onclose = (event) => {
                console.log('[NarratorUI] WebSocket closed:', event.code);
                this.updateStatus('disconnected');
                this.scheduleReconnect();
            };

            this.ws.onerror = (error) => {
                console.error('[NarratorUI] WebSocket error:', error);
            };

            this.ws.onmessage = (event) => {
                try {
                    const msg = JSON.parse(event.data);
                    this.handleMessage(msg);
                } catch (err) {
                    console.error('[NarratorUI] Failed to parse message:', err);
                }
            };
        } catch (err) {
            console.error('[NarratorUI] Failed to connect:', err);
            this.scheduleReconnect();
        }
    }

    scheduleReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.log('[NarratorUI] Max reconnect attempts reached');
            return;
        }

        this.reconnectAttempts++;
        const delay = Math.min(this.reconnectDelay * Math.pow(1.5, this.reconnectAttempts - 1), 30000);

        console.log(`[NarratorUI] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);

        setTimeout(() => this.connectWebSocket(), delay);
    }

    updateStatus(status) {
        if (!this.statusElement) return;

        this.statusElement.className = 'connection-status ' + status;
        const textElement = this.statusElement.querySelector('.status-text');
        if (textElement) {
            switch (status) {
                case 'connected':
                    textElement.textContent = 'Connected';
                    break;
                case 'disconnected':
                    textElement.textContent = 'Disconnected';
                    break;
                case 'connecting':
                    textElement.textContent = 'Connecting...';
                    break;
            }
        }
    }

    handleMessage(msg) {
        console.log('[NarratorUI] Received:', msg.type);

        switch (msg.type) {
            case 'connection_ack':
                // Initial connection acknowledgment
                this.updateQueueCount(msg.queue_length);
                this.updateRateLimitStatus(msg.tts_available);
                break;

            case 'queue_update':
                // Queue state changed - trigger HTMX refresh
                this.updateQueueCount(msg.queue_length);
                htmx.trigger('#queue-list', 'refresh');
                htmx.trigger('#queue-status', 'refresh');
                break;

            case 'rate_limit_status':
                // Rate limit countdown update
                this.updateRateLimitDisplay(msg);
                break;

            case 'narration_start':
                // Narration started - update test result if on test page
                this.onNarrationStart(msg);
                break;

            case 'narration_end':
                // Narration completed
                this.onNarrationEnd(msg);
                break;

            case 'narration_error':
                // Narration failed
                this.onNarrationError(msg);
                break;

            case 'ping':
                // Respond to server ping
                this.sendPong();
                break;
        }
    }

    updateQueueCount(count) {
        const countElements = document.querySelectorAll('.queue-count');
        countElements.forEach(el => {
            el.textContent = count;
        });
    }

    updateRateLimitStatus(available) {
        const statusElements = document.querySelectorAll('.tts-status-indicator');
        statusElements.forEach(el => {
            el.classList.toggle('ready', available);
            el.classList.toggle('waiting', !available);
        });
    }

    updateRateLimitDisplay(msg) {
        const countdownElements = document.querySelectorAll('.tts-countdown');
        countdownElements.forEach(el => {
            if (msg.tts_available) {
                el.textContent = 'Ready';
                el.classList.add('ready');
            } else {
                const seconds = msg.tts_available_in_seconds;
                el.textContent = seconds !== null ? seconds.toFixed(1) + 's' : 'Waiting...';
                el.classList.remove('ready');
            }
        });

        this.updateQueueCount(msg.queue_length);
        this.updateRateLimitStatus(msg.tts_available);
    }

    onNarrationStart(msg) {
        // Show notification
        this.showToast(`Narrating for ${msg.user}...`, 'info');

        // Update queue item if visible
        const queueItem = document.querySelector(`[data-item-id="${msg.id}"]`);
        if (queueItem) {
            queueItem.classList.add('processing');
        }
    }

    onNarrationEnd(msg) {
        // Update queue item if visible
        const queueItem = document.querySelector(`[data-item-id="${msg.id}"]`);
        if (queueItem) {
            queueItem.classList.remove('processing');
            // Trigger refresh to remove completed item
            htmx.trigger('#queue-list', 'refresh');
        }
    }

    onNarrationError(msg) {
        this.showToast(`Error: ${msg.error}`, 'error');

        // Update queue item if visible
        const queueItem = document.querySelector(`[data-item-id="${msg.id}"]`);
        if (queueItem) {
            queueItem.classList.remove('processing');
            queueItem.classList.add('error');
        }
    }

    sendPong() {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'pong' }));
        }
    }

    showToast(message, type = 'success') {
        const container = document.getElementById('toast-container');
        if (!container) return;

        const toast = document.createElement('div');
        toast.className = 'toast ' + type;
        toast.textContent = message;
        container.appendChild(toast);

        setTimeout(() => toast.remove(), 3000);
    }
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    window.narratorUI = new NarratorUI();
});

// Visibility change handler - reconnect when page becomes visible
document.addEventListener('visibilitychange', () => {
    if (!document.hidden && window.narratorUI) {
        const ws = window.narratorUI.ws;
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            window.narratorUI.connectWebSocket();
        }
    }
});
