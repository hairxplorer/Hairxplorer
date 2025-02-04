class ProhairWidget {
  constructor(apiKey) {
    this.apiKey = apiKey;
    this.initForm();
  }

  initForm() {
    const formHTML = `
      <div class="prohair-widget">
        <form id="prohair-form">
          <input type="text" name="name" placeholder="Nom" required>
          <input type="tel" name="phone" placeholder="Téléphone" required>
          <input type="email" name="email" placeholder="Email" required>
          <textarea name="message" placeholder="Message"></textarea>
          <div class="photo-upload">
            <input type="file" accept="image/*" name="front" required>
            <input type="file" accept="image/*" name="top" required>
            <input type="file" accept="image/*" name="side" required>
            <input type="file" accept="image/*" name="back" required>
          </div>
          <label>
            <input type="checkbox" name="consent" required>
            J'accepte les <a href="#" class="privacy-link">conditions d'utilisation</a>
          </label>
          <button type="submit">Obtenir l'estimation</button>
        </form>
        <div class="result"></div>
      </div>
    `;
    const container = document.getElementById('widget-container');
    if (container) {
      container.innerHTML = formHTML;
    } else {
      document.body.insertAdjacentHTML('beforeend', formHTML);
    }
    this.bindEvents();
  }

  bindEvents() {
    document.getElementById('prohair-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const formData = new FormData(e.target);
      formData.append('api_key', this.apiKey);
      try {
        const result = await this.sendAnalysis(formData);
        this.displayResult(result);
      } catch (error) {
        console.error('Erreur:', error);
      }
    });
  }

  async sendAnalysis(formData) {
    const response = await fetch('https://hairxplorer-production.up.railway.app/analyze', {
      method: 'POST',
      body: formData
    });
    return response.json();
  }

  displayResult(data) {
    document.querySelector('.result').innerHTML = `
      <h3>Estimation : ${data.price_range || "N/A"}</h3>
      <p>${data.details || "Aucun détail disponible"}</p>
    `;
  }
}
