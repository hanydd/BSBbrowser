$('form').submit(function() {
    $(':input', this).each(function() {
        this.disabled = !($(this).val());
    });
});

$('.formreset').click(resetForm);
function resetForm() {
    const $form = $('#filterForm')
    $form.find('input:text, input:password, input:file, select, textarea').val('');
    $form.find(':input[type=number]').val('');
    $form.find('input:radio, input:checkbox')
         .removeAttr('checked').removeAttr('selected');
}

document.querySelector("#darkmode").onclick = function(){
    darkmode.toggleDarkMode();
}

const elements = document.querySelectorAll('.clip')
for (let i = 0, element; element = elements[i]; i++) {
    element.addEventListener('click', function() {
        navigator.clipboard.writeText(element.dataset.value);
    });
}

window.onpagehide = function(){};

// Cursor-follow glow for search buttons
document.addEventListener('DOMContentLoaded', () => {
  const searchButtons = document.querySelectorAll('.search-card .btn-primary');
  searchButtons.forEach((btn) => {
    btn.addEventListener('mousemove', (e) => {
      const rect = btn.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      btn.style.setProperty('--glow-x', `${x}px`);
      btn.style.setProperty('--glow-y', `${y}px`);
    });
  });
});