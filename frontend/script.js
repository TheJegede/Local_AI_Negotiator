/**
 * AI Negotiation Frontend - Main Application Logic
 * Handles session management, message flow, agreement detection, and evaluation
 */

// Global state
let api = null;
let dealParams = null;
let conversationHistory = [];
let sessionId = null;
let negotiationState = 'SETUP'; // SETUP, NEGOTIATING, CLOSING, EVALUATION

/**
 * Escape HTML to prevent XSS when inserting into innerHTML contexts
 */
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

/**
 * Initialize API and event listeners on page load
 */
document.addEventListener('DOMContentLoaded', function() {
  // Initialize API (it's loaded as a global from api.js script tag in HTML)
  if (typeof NegotiationAPI !== 'undefined') {
    api = new NegotiationAPI();
    console.log('API initialized:', api);
  } else {
    console.error('API class not found. Check api.js is loaded.');
  }

  // Event Listeners for Chat
  const newChatBtn = document.getElementById('new-chat-btn');
  const sendBtn = document.getElementById('send-btn');
  const userInput = document.getElementById('user-input');
  
  if (newChatBtn) newChatBtn.addEventListener('click', startNewNegotiation);
  renderChatHistory();
  if (sendBtn) sendBtn.addEventListener('click', sendMessage);
  if (userInput) userInput.addEventListener('keypress', handleKeyPress);

  // Radio button handlers for parameter source
  const radioButtons = document.querySelectorAll('input[name="seedParams"]');
  radioButtons.forEach(radio => {
    radio.addEventListener('change', handleParameterSourceChange);
  });

  // --- NEW CODE: INTRO MODAL LOGIC ---
  const startBtn = document.getElementById('start-btn');
  const introOverlay = document.getElementById('intro-overlay');
  
  if (startBtn && introOverlay) {
      startBtn.addEventListener('click', () => {
          // Fade out effect
          introOverlay.style.opacity = '0';
          introOverlay.style.transition = 'opacity 0.5s ease';
          
          // Remove from screen after fade
          setTimeout(() => {
              introOverlay.style.display = 'none';
          }, 500);
      });
  }
  // -----------------------------------

  console.log('Event listeners registered');

  // Automatically start a session in the background so it's ready when they click "Begin"
  startNewNegotiation(); 
});

/**
 * Handle parameter source selection (Random vs Student ID)
 */
function handleParameterSourceChange(e) {
  const studentIdGroup = document.getElementById('student-id-group');
  if (e.target.value === 'student') {
    studentIdGroup.style.display = 'flex';
  } else {
    studentIdGroup.style.display = 'none';
  }
}

/**
 * Start new negotiation session
 */
async function startNewNegotiation() {
  try {
    negotiationState = 'NEGOTIATING';

    // Get student ID if Student ID mode is selected
    const seedMode = document.querySelector('input[name="seedParams"]:checked')?.value;
    let studentId = null;

    if (seedMode === 'student') {
      const studentInput = document.getElementById('student-id-input');
      studentId = studentInput ? studentInput.value : null;
    }

    // Disable button during loading
    const newChatBtn = document.getElementById('new-chat-btn');
    if (newChatBtn) {
        newChatBtn.disabled = true;
        newChatBtn.textContent = 'Starting...';
    }

    // Create new session
    if (!api) api = new NegotiationAPI(); // Safety check
    const session = await api.createSession(studentId);
    
    // Update State
    sessionId = session.session_id;
    // NEW: Save to History
    saveToHistoryList(sessionId, `Negotiation ${new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}`);
    renderChatHistory();
    api.sessionId = sessionId; 
    dealParams = session.deal_params;
    
    // API v2 returns greeting directly
    const greeting = session.greeting || "Hello! Let's negotiate.";
    
    console.log('Session created:', sessionId);

    // Clear chat history from UI
    clearChatHistory();

    // Display greeting
    displayMessage('assistant', greeting);

    // Show deal status section & reset metrics
    const dealStatus = document.getElementById('deal-status');
    if (dealStatus) dealStatus.classList.remove('hidden');
    resetMetrics();

  } catch (error) {
    console.error('Error starting negotiation:', error);
    // Note: We don't show an error alert here because it runs on load. 
    // If it fails, the user will just see an empty chat and can click "New Chat" manually.
  } finally {
    // Re-enable button
    const newChatBtn = document.getElementById('new-chat-btn');
    if (newChatBtn) {
        newChatBtn.disabled = false;
        newChatBtn.textContent = '↻ New Chat';
    }
  }
}

/**
 * Handle Enter key in message input
 */
function handleKeyPress(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

/**
 * Send user message and get AI response
 */
async function sendMessage() {
  const input = document.getElementById('user-input');
  const userMessage = input.value.trim();

  // FIX: Don't just return if no session. Check if we need to start one.
  if (!userMessage) return;
  
  // If user types before session loads, wait for it or start it
  if (!sessionId) {
      console.log("No session found, starting one now...");
      await startNewNegotiation();
      if (!sessionId) return; // If still failed, stop
  }

  try {
    // Disable send button during processing
    const sendBtn = document.getElementById('send-btn');
    if (sendBtn) sendBtn.disabled = true;

    // Display user message
    displayMessage('user', userMessage);
    input.value = '';
    input.style.height = 'auto'; // Reset textarea height
    
    // Disable input while thinking
    input.disabled = true;

    // Get AI response
    const result = await api.sendMessage(userMessage);
    displayMessage('assistant', result.ai_response);

    // 1. Update Metrics if terms were proposed
    if (result.proposed_terms) {
      updateDealMetrics(result.proposed_terms);
    }

    // 2. Check if Backend flagged "Deal Ready"
    if (result.deal_ready && result.proposed_terms) {
       negotiationState = 'CLOSING';
       handleAgreementDetected(result.proposed_terms);
    }

  } catch (error) {
    console.error('Error sending message:', error);
    displayMessage('error', 'Failed to send message. Please try again.');
  } finally {
    const sendBtn = document.getElementById('send-btn');
    if (sendBtn) sendBtn.disabled = false;
    
    input.disabled = false;
    input.focus();
  }
}

/**
 * Display message in chat window
 */
function displayMessage(role, content) {
  const chatHistory = document.getElementById('chat-history');
  if (!chatHistory) return;

  const messageDiv = document.createElement('div');
  messageDiv.className = `message ${role}`;

  const contentDiv = document.createElement('div');
  contentDiv.className = 'message-content';
  
  // Safe rendering: use textContent to prevent XSS, pre-wrap preserves line breaks
  contentDiv.textContent = content || '';
  contentDiv.style.whiteSpace = 'pre-wrap';

  messageDiv.appendChild(contentDiv);
  chatHistory.appendChild(messageDiv);

  // Auto-scroll to bottom
  chatHistory.scrollTop = chatHistory.scrollHeight;
}

/**
 * Clear chat history from UI
 */
function clearChatHistory() {
  const chatHistory = document.getElementById('chat-history');
  if (chatHistory) chatHistory.innerHTML = '';
}

/**
 * Reset metric displays
 */
function resetMetrics() {
    const p = document.getElementById('metric-price');
    const d = document.getElementById('metric-delivery');
    const v = document.getElementById('metric-volume');
    if(p) p.textContent = '--';
    if(d) d.textContent = '--';
    if(v) v.textContent = '--';
}

/**
 * Update deal metrics display
 */
function updateDealMetrics(terms) {
  const priceElement = document.getElementById('metric-price');
  const deliveryElement = document.getElementById('metric-delivery');
  const volumeElement = document.getElementById('metric-volume');

  if (terms.price && priceElement) {
    priceElement.textContent = `$${terms.price}`;
    priceElement.style.color = 'var(--success-text, #16a34a)';
  }
  if (terms.delivery && deliveryElement) {
    deliveryElement.textContent = `${terms.delivery} days`;
    deliveryElement.style.color = 'var(--success-text, #16a34a)';
  }
  if (terms.volume && volumeElement) {
    volumeElement.textContent = terms.volume.toLocaleString() + ' units';
    volumeElement.style.color = 'var(--success-text, #16a34a)';
  }
}

/**
 * Handle agreement detection - show confirmation dialog
 */
function handleAgreementDetected(agreedTerms) {
  // Remove existing modals if any
  const existingModal = document.querySelector('.confirmation-modal');
  if (existingModal) existingModal.remove();

  // Create and display confirmation modal
  const modal = document.createElement('div');
  modal.className = 'confirmation-modal';
  modal.style.cssText = `
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  `;

  const box = document.createElement('div');
  box.className = 'confirmation-box';
  box.style.cssText = `
    background: white;
    border-radius: 12px;
    padding: 2rem;
    max-width: 400px;
    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
  `;

  box.innerHTML = `
    <h3 style="color: #333; margin-bottom: 1rem;">✅ Deal Confirmed!</h3>
    <div style="background: #f0fdf4; padding: 1rem; border-radius: 8px; margin-bottom: 1.5rem; font-size: 0.95rem;">
      <p style="margin-bottom: 0.5rem;"><strong>Price:</strong> $${agreedTerms.price} per unit</p>
      <p style="margin-bottom: 0.5rem;"><strong>Delivery:</strong> ${agreedTerms.delivery} days</p>
      <p><strong>Volume:</strong> ${agreedTerms.volume?.toLocaleString() || 'Standard'} units</p>
    </div>
    <div style="display: flex; gap: 1rem;">
      <button id="confirm-deal-btn" style="
        flex: 1;
        background: #16a34a;
        color: white;
        border: none;
        padding: 0.75rem;
        border-radius: 6px;
        cursor: pointer;
        font-weight: 500;
      ">Finalize & Grade</button>
      <button id="continue-negotiating-btn" style="
        flex: 1;
        background: #e5e7eb;
        color: #374151;
        border: none;
        padding: 0.75rem;
        border-radius: 6px;
        cursor: pointer;
        font-weight: 500;
      ">Continue Negotiating</button>
    </div>
  `;

  modal.appendChild(box);
  document.body.appendChild(modal);

  // Event handlers
  const confirmBtn = document.getElementById('confirm-deal-btn');
  const continueBtn = document.getElementById('continue-negotiating-btn');

  if (confirmBtn) {
      confirmBtn.addEventListener('click', async () => {
        // Show loading state
        confirmBtn.textContent = "Grading...";
        confirmBtn.disabled = true;
        await finalizeDeal(agreedTerms);
        modal.remove();
      });
  }

  if (continueBtn) {
      continueBtn.addEventListener('click', () => {
        modal.remove();
        negotiationState = 'NEGOTIATING';
        const input = document.getElementById('user-input');
        if (input) input.focus();
      });
  }
}

/**
 * Finalize deal and trigger evaluation
 */
async function finalizeDeal(agreedTerms) {
  try {
    negotiationState = 'EVALUATION';

    displayMessage('assistant', 'Excellent! Deal confirmed. Generating your performance evaluation...');

    // Fetch evaluation from backend
    const result = await api.evaluateDeal(agreedTerms);

    // Display evaluation report
    displayEvaluation(result.evaluation_report);

  } catch (error) {
    console.error('Error finalizing deal:', error);
    displayMessage('error', 'Failed to generate evaluation. Please try again.');
  }
}

/**
 * Display comprehensive evaluation report
 */
function displayEvaluation(reportText) {
  const modal = document.createElement('div');
  modal.className = 'evaluation-modal';
  modal.style.cssText = `
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    overflow-y: auto;
  `;

  const box = document.createElement('div');
  box.style.cssText = `
    background: white;
    border-radius: 12px;
    padding: 2rem;
    max-width: 600px;
    width: 90%;
    margin: 2rem auto;
    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
    max-height: 80vh;
    overflow-y: auto;
  `;

  box.innerHTML = `
    <h2 style="color: #333; margin-bottom: 1.5rem; text-align: center;">📊 Negotiation Report</h2>

    <div style="background: #f9fafb; padding: 1.5rem; border-radius: 8px; margin-bottom: 2rem; font-size: 1rem; line-height: 1.6; white-space: pre-wrap; font-family: sans-serif;">
${escapeHtml(reportText)}
    </div>

    <button id="new-negotiation-btn" style="
      width: 100%;
      background: #2563eb;
      color: white;
      border: none;
      padding: 1rem;
      border-radius: 6px;
      cursor: pointer;
      font-weight: 600;
      font-size: 1rem;
    ">Start New Negotiation</button>
  `;

  modal.appendChild(box);
  document.body.appendChild(modal);

  const newBtn = document.getElementById('new-negotiation-btn');
  if (newBtn) {
      newBtn.addEventListener('click', () => {
        modal.remove();
        location.reload(); // Fresh start
      });
  }
}

// --- HISTORY MANAGEMENT FUNCTIONS ---

const HISTORY_KEY = 'negotiator_sessions';

function saveToHistoryList(id, title) {
    const history = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
    // Add new chat to top
    history.unshift({ id, title, date: new Date().toISOString() });
    // Keep last 15 chats
    if (history.length > 15) history.pop();
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
}

function renderChatHistory() {
    const listContainer = document.getElementById('chat-list');
    if (!listContainer) return;
    
    const history = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
    listContainer.innerHTML = '';

    if (history.length === 0) {
        listContainer.innerHTML = '<div style="padding:10px; color:#94a3b8; font-size:0.85rem; text-align:center;">No recent chats</div>';
        return;
    }

    history.forEach(item => {
        const div = document.createElement('div');
        div.className = `chat-item ${item.id === sessionId ? 'active' : ''}`;
        div.innerHTML = `
            <i class="fa-regular fa-comments"></i>
            <span style="overflow:hidden; text-overflow:ellipsis;">${item.title}</span>
        `;
        div.onclick = () => loadPreviousSession(item.id);
        listContainer.appendChild(div);
    });
}

async function loadPreviousSession(id) {
    if (id === sessionId) return;

    // UI Feedback
    const listContainer = document.getElementById('chat-list');
    listContainer.style.opacity = '0.5';

    try {
        // Load data from backend
        const sessionData = await api.getSession(id);
        
        sessionId = sessionData.session_id;
        api.sessionId = sessionId;
        dealParams = sessionData.deal_params;
        
        // Clear UI
        clearChatHistory();
        
        // Replay messages
        if (sessionData.history && Array.isArray(sessionData.history)) {
            sessionData.history.forEach(msg => {
                displayMessage(msg.role, msg.content);
            });
        }
        
        // Restore Deal Status if available (Optional improvement)
        if (dealParams) {
             // Reset metrics display to hide old values or show defaults
             resetMetrics();
        }

        // Update Sidebar Highlight
        renderChatHistory();
        
    } catch (error) {
        console.error("Failed to load session", error);
        alert("Could not load this chat history.");
    } finally {
        listContainer.style.opacity = '1';
    }
}


