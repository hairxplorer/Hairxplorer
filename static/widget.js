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
      En cliquant sur "Analyser", j'accepte que mes données soient utilisées pour me recontacter et que je reçoive une copie de l'analyse.
    </label>
    <button type="submit">Obtenir l'estimation</button>
  </form>
  <div class="result"></div>
</div>
