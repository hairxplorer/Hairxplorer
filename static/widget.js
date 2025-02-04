class ProhairWidget {
  constructor(apiKey) {
    this.apiKey = apiKey;
    console.log("ProhairWidget initialisé avec apiKey :", this.apiKey);
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
      console.log("Formulaire injecté dans #widget-container");
    } else {
      document.body.insertAdjacentHTML('beforeend', formHTML);
      console.log("Formulaire injecté dans le body");
    }
    this.bindEvents();
  }

  bindEvents() {
    const form = document.getElementById('prohair-form');
    if (!form) {
      console.error("Formulaire non trouvé pour le binding des événements");
      return;
    }
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      console.log("Soumission du formulaire détectée");
      const formData = new FormData(e.target);
      formData.append('api_key', this.apiKey);
      try {
        const result = await this.sendAnalysis(formData);
        console.log("Réponse de l'API :", result);
        this.displayResult(result);
      } catch (error) {
        console.error('Erreur lors de l\'envoi de l\'analyse :', error);
        document.querySelector('.result').innerHTML = `<p style="color:red;">Erreur: ${error}</p>`;
      }
    });
  }

  async sendAnalysis(formData) {
    console.log("Envoi des données au endpoint /analyze");
    const response = await fetch('https://hairxplorer-production.up.railway.app/analyze', {
      method: 'POST',
      body: formData
    });
    if (!response.ok) {
      const errorText = await response.text();
      console.error("Erreur fetch:", errorText);
      throw new Error("Erreur réseau lors de l'appel à l'API");
    }
    const jsonResponse = await response.json();
    console.log("jsonResponse reçue :", jsonResponse);
    return jsonResponse;
  }

  displayResult(data) {
    // Affichage du résultat dans le conteneur .result
    console.log("Affichage du résultat:", data);
    document.querySelector('.result').innerHTML = `
      <h3>Estimation : ${data.price_range || "N/A"}</h3>
      <p>${data.details || "Aucun détail disponible"}</p>
    `;
  }
}
