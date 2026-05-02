/**
 * API Client for AI Negotiation Backend
 * Handles all communication with FastAPI backend
 */

class NegotiationAPI {
  constructor() {
    this.sessionId = null;
    // Determine API URL based on environment
    this.baseUrl = this.getBaseUrl();
    console.log('API Base URL:', this.baseUrl);
  }

  /**
   * Get API base URL based on environment
   */
  getBaseUrl() {
    // Check for environment variable first
    if (typeof process !== 'undefined' && process.env.REACT_APP_API_URL) {
      return process.env.REACT_APP_API_URL;
    }

    // Check for environment variable override
    if (typeof window !== 'undefined' && window.REACT_APP_API_URL) {
      return window.REACT_APP_API_URL;
    }

    // Check for localStorage override (useful for testing)
    const storedUrl = localStorage.getItem('API_BASE_URL');
    if (storedUrl) {
      return storedUrl;
    }

    // Production URL loaded from config.js (window.APP_CONFIG.API_URL)
    const PRODUCTION_API_URL = window.APP_CONFIG && window.APP_CONFIG.API_URL;

    // If in production (https), require config.js to have API_URL set
    if (window.location.protocol === 'https:') {
      if (!PRODUCTION_API_URL) {
        console.error('Production deployment requires API_URL to be set in config.js');
      }
      return PRODUCTION_API_URL || '';
    }

    // For localhost development
    if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
      return 'http://localhost:8000';
    }

    return PRODUCTION_API_URL || '';
  }

  /**
   * Override API URL (useful for testing/debugging)
   */
  setBaseUrl(url) {
    this.baseUrl = url;
    localStorage.setItem('API_BASE_URL', url);
    console.log('API URL updated to:', url);
  }

  /**
   * Make HTTP request with error handling
   */
  async request(endpoint, method = 'GET', body = null) {
    const url = `${this.baseUrl}${endpoint}`;

    const options = {
      method,
      headers: {
        'Content-Type': 'application/json',
      },
      mode: 'cors',
    };

    if (body) {
      options.body = JSON.stringify(body);
    }

    try {
      const response = await fetch(url, options);

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: response.statusText }));

        // Handle complex FastAPI error objects
        const errorMessage = typeof errorData.detail === 'object'
          ? JSON.stringify(errorData.detail)
          : (errorData.detail || `HTTP ${response.status}: ${response.statusText}`);

        throw new Error(errorMessage);
      }

      return await response.json();
    } catch (error) {
      console.error(`API Error [${method} ${endpoint}]:`, error.message);
      throw error;
    }
  }

  /**
   * Create new negotiation session
   * @param {string|null} studentId - Optional student ID for reproducible parameters
   * @returns {Promise<Object>} Session data with greeting and parameters
   */
  async createSession(studentId = null) {
    const body = { student_id: studentId };

    // FIX: Changed to send body data correctly instead of URL params
    const data = await this.request('/api/sessions/new', 'POST', body);

    this.sessionId = data.session_id;
    return data;
  }

  /**
   * Send user message and get AI response
   * @param {string} userInput - User's message/offer
   * @returns {Promise<Object>} AI response with agreement status
   */
  async sendMessage(userInput) {
    if (!this.sessionId) {
      throw new Error('No active session. Create a session first.');
    }

    return this.request('/api/chat', 'POST', {
      session_id: this.sessionId,
      user_input: userInput,
    });
  }

  /**
   * Retrieve complete session data
   * @returns {Promise<Object>} Full session with history
   */
  /**
   * UPDATED: Retrieve session data (Current or History)
   */
  async getSession(sessionId = null) {
    // If an ID is provided (history), use it. Otherwise use current session.
    const targetId = sessionId || this.sessionId;

    if (!targetId) {
      throw new Error('No session ID provided.');
    }

    // Reuse your existing request helper
    return this.request(`/api/sessions/${targetId}`, 'GET');
  }

  /**
   * Get evaluation for completed negotiation
   * UPDATED: Now accepts finalTerms and sends to /api/evaluate
   */
  async evaluateDeal(finalTerms) {
    if (!this.sessionId) {
      throw new Error('No active session.');
    }

    return this.request('/api/evaluate', 'POST', {
      session_id: this.sessionId,
      final_terms: finalTerms
    });
  }

  /**
   * Delete/close a session
   */
  async deleteSession() {
    if (!this.sessionId) {
      throw new Error('No active session.');
    }

    const result = await this.request(`/api/sessions/${this.sessionId}`, 'DELETE');
    this.sessionId = null;
    return result;
  }

  /**
   * List all active sessions (admin/testing)
   */
  async listSessions() {
    return this.request('/api/sessions');
  }

  /**
   * Check API health/connectivity
   */
  async checkHealth() {
    try {
      const response = await this.request('/'); // Changed to root to match main.py
      return response.status === 'running';
    } catch (error) {
      console.warn('API health check failed:', error.message);
      return false;
    }
  }
}

// Make class available globally when loaded as script tag
if (typeof window !== 'undefined') {
  window.NegotiationAPI = NegotiationAPI;
}

// Also support CommonJS/ES6 export if used as module
if (typeof module !== 'undefined' && module.exports) {
  module.exports = NegotiationAPI;
}


