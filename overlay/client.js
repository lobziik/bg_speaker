/**
 * Narrator Overlay WebSocket Client
 *
 * Connects to the narrator bot server via WebSocket and handles:
 * - Audio playback
 * - Subtitle display with animations
 * - Queue status updates
 * - Automatic reconnection
 */

class NarratorOverlay {
    constructor() {
        // Configuration
        this.wsUrl = this.getWebSocketUrl();
        this.reconnectDelay = 1000;
        this.maxReconnectDelay = 30000;
        this.pingTimeout = 45000; // Server pings every 30s

        // State
        this.ws = null;
        this.reconnectAttempts = 0;
        this.currentNarration = null;
        this.pingTimer = null;

        // DOM elements
        this.subtitleContainer = document.getElementById('subtitle-container');
        this.subtitleUser = document.getElementById('subtitle-user');
        this.subtitleText = document.getElementById('subtitle-text');
        this.queueIndicator = document.getElementById('queue-indicator');
        this.queueCount = document.getElementById('queue-count');
        this.connectionStatus = document.getElementById('connection-status');
        this.statusText = this.connectionStatus.querySelector('.status-text');
        this.audioPlayer = document.getElementById('audio-player');

        // Check for debug mode
        this.debug = new URLSearchParams(window.location.search).has('debug');
        if (this.debug) {
            document.body.classList.add('debug');
            console.log('[Narrator] Debug mode enabled');
        }

        // Audio context for proper playback
        this.audioContext = null;

        // Start connection
        this.connect();
    }

    /**
     * Determine WebSocket URL based on current page location
     */
    getWebSocketUrl() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.host;
        return `${protocol}//${host}/ws/overlay`;
    }

    /**
     * Connect to WebSocket server
     */
    connect() {
        this.updateConnectionStatus('connecting');
        this.log('Connecting to', this.wsUrl);

        try {
            this.ws = new WebSocket(this.wsUrl);
            this.ws.onopen = () => this.handleOpen();
            this.ws.onclose = (event) => this.handleClose(event);
            this.ws.onerror = (error) => this.handleError(error);
            this.ws.onmessage = (event) => this.handleMessage(event);
        } catch (error) {
            this.log('Connection error:', error);
            this.scheduleReconnect();
        }
    }

    /**
     * Handle WebSocket open
     */
    handleOpen() {
        this.log('Connected');
        this.reconnectAttempts = 0;
        this.reconnectDelay = 1000;
        this.updateConnectionStatus('connected');
        this.resetPingTimer();

        // Initialize audio context on first connection
        // (must be done after user interaction in some browsers)
        if (!this.audioContext) {
            this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
        }
    }

    /**
     * Handle WebSocket close
     */
    handleClose(event) {
        this.log('Disconnected:', event.code, event.reason);
        this.updateConnectionStatus('disconnected');
        this.clearPingTimer();
        this.scheduleReconnect();
    }

    /**
     * Handle WebSocket error
     */
    handleError(error) {
        this.log('WebSocket error:', error);
    }

    /**
     * Handle incoming WebSocket message
     */
    handleMessage(event) {
        this.resetPingTimer();

        try {
            const message = JSON.parse(event.data);
            this.log('Received:', message.type);

            switch (message.type) {
                case 'connection_ack':
                    this.handleConnectionAck(message);
                    break;
                case 'ping':
                    this.handlePing(message);
                    break;
                case 'narration_start':
                    this.handleNarrationStart(message);
                    break;
                case 'audio_data':
                    this.handleAudioData(message);
                    break;
                case 'narration_end':
                    this.handleNarrationEnd(message);
                    break;
                case 'narration_error':
                    this.handleNarrationError(message);
                    break;
                case 'queue_update':
                    this.handleQueueUpdate(message);
                    break;
                case 'rate_limit_status':
                    this.handleRateLimitStatus(message);
                    break;
                default:
                    this.log('Unknown message type:', message.type);
            }
        } catch (error) {
            this.log('Error parsing message:', error);
        }
    }

    /**
     * Handle connection acknowledgment
     */
    handleConnectionAck(message) {
        this.log('Connection acknowledged, queue length:', message.queue_length);
        this.updateQueueIndicator(message.queue_length);
    }

    /**
     * Handle ping - respond with pong
     */
    handlePing(message) {
        this.send({
            type: 'pong',
            timestamp: message.timestamp
        });
    }

    /**
     * Handle narration start - display subtitle
     */
    handleNarrationStart(message) {
        this.log('Narration start:', message.id, message.user);

        this.currentNarration = {
            id: message.id,
            user: message.user,
            text: message.text,
            startTime: Date.now()
        };

        // Display subtitle
        this.showSubtitle(message.user, message.text);
    }

    /**
     * Handle audio data - play audio
     */
    handleAudioData(message) {
        if (!this.currentNarration || this.currentNarration.id !== message.id) {
            this.log('Audio data for unknown narration:', message.id);
            return;
        }

        this.log('Playing audio:', message.id, 'duration:', message.duration_ms);

        // Decode base64 audio
        const audioData = this.base64ToArrayBuffer(message.data);

        // Play the audio
        this.playAudio(audioData, message.duration_ms);
    }

    /**
     * Handle narration end - hide subtitle
     */
    handleNarrationEnd(message) {
        this.log('Narration end:', message.id);

        if (this.currentNarration && this.currentNarration.id === message.id) {
            this.hideSubtitle();
            this.currentNarration = null;
        }
    }

    /**
     * Handle narration error
     */
    handleNarrationError(message) {
        this.log('Narration error:', message.id, message.error);

        // Optionally display error to viewers
        // For now, just hide any current subtitle
        if (this.currentNarration && this.currentNarration.id === message.id) {
            this.hideSubtitle();
            this.currentNarration = null;
        }
    }

    /**
     * Handle queue update
     */
    handleQueueUpdate(message) {
        this.log('Queue update:', message.event, 'length:', message.queue_length);
        this.updateQueueIndicator(message.queue_length);
    }

    /**
     * Handle rate limit status
     */
    handleRateLimitStatus(message) {
        this.log('Rate limit status:', message.tts_available ? 'available' : 'limited');
        this.updateQueueIndicator(message.queue_length);
    }

    /**
     * Show subtitle with animation
     */
    showSubtitle(user, text) {
        // Set content
        this.subtitleUser.textContent = user;
        this.subtitleText.textContent = text;

        // Remove fading class if present
        this.subtitleContainer.classList.remove('fading-out', 'hidden');

        // Add visible class
        this.subtitleContainer.classList.add('visible');
    }

    /**
     * Hide subtitle with animation
     */
    hideSubtitle() {
        this.subtitleContainer.classList.add('fading-out');

        // After animation, hide completely
        setTimeout(() => {
            this.subtitleContainer.classList.remove('visible', 'fading-out');
            this.subtitleContainer.classList.add('hidden');
        }, 500);
    }

    /**
     * Update queue indicator
     */
    updateQueueIndicator(count) {
        this.queueCount.textContent = count;

        if (count > 0) {
            this.queueIndicator.classList.add('visible');
            this.queueIndicator.classList.remove('hidden');
        } else {
            this.queueIndicator.classList.remove('visible');
            this.queueIndicator.classList.add('hidden');
        }
    }

    /**
     * Convert base64 string to ArrayBuffer
     */
    base64ToArrayBuffer(base64) {
        const binaryString = atob(base64);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
            bytes[i] = binaryString.charCodeAt(i);
        }
        return bytes.buffer;
    }

    /**
     * Play audio from ArrayBuffer
     */
    playAudio(audioData, durationMs) {
        // Create a blob URL for the audio
        const blob = new Blob([audioData], { type: 'audio/wav' });
        const url = URL.createObjectURL(blob);

        // Set audio source and play
        this.audioPlayer.src = url;
        this.audioPlayer.play().catch(error => {
            this.log('Audio playback error:', error);
            // Try to resume audio context if it was suspended
            if (this.audioContext && this.audioContext.state === 'suspended') {
                this.audioContext.resume();
            }
        });

        // Clean up blob URL after playback
        this.audioPlayer.onended = () => {
            URL.revokeObjectURL(url);
        };
    }

    /**
     * Send message to server
     */
    send(message) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
        }
    }

    /**
     * Update connection status indicator
     */
    updateConnectionStatus(status) {
        this.connectionStatus.className = status;

        switch (status) {
            case 'connected':
                this.statusText.textContent = 'Connected';
                break;
            case 'disconnected':
                this.statusText.textContent = 'Disconnected';
                break;
            case 'connecting':
                this.statusText.textContent = 'Connecting...';
                break;
        }
    }

    /**
     * Schedule reconnection with exponential backoff
     */
    scheduleReconnect() {
        const delay = Math.min(
            this.reconnectDelay * Math.pow(2, this.reconnectAttempts),
            this.maxReconnectDelay
        );

        this.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts + 1})`);

        setTimeout(() => {
            this.reconnectAttempts++;
            this.connect();
        }, delay);
    }

    /**
     * Reset ping timeout timer
     */
    resetPingTimer() {
        this.clearPingTimer();
        this.pingTimer = setTimeout(() => {
            this.log('Ping timeout - reconnecting');
            if (this.ws) {
                this.ws.close();
            }
        }, this.pingTimeout);
    }

    /**
     * Clear ping timer
     */
    clearPingTimer() {
        if (this.pingTimer) {
            clearTimeout(this.pingTimer);
            this.pingTimer = null;
        }
    }

    /**
     * Log message (only in debug mode or for errors)
     */
    log(...args) {
        if (this.debug) {
            console.log('[Narrator]', ...args);
        }
    }
}

// Initialize overlay when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.narratorOverlay = new NarratorOverlay();
});

// Handle visibility change - reconnect when page becomes visible
document.addEventListener('visibilitychange', () => {
    if (!document.hidden && window.narratorOverlay) {
        const overlay = window.narratorOverlay;
        if (overlay.ws && overlay.ws.readyState !== WebSocket.OPEN) {
            overlay.connect();
        }
    }
});
