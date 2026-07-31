document.addEventListener('DOMContentLoaded', function () {
  const sidebar   = document.getElementById('dbSidebar');
  const backdrop  = document.getElementById('dbSidebarBackdrop');
  const openBtn   = document.getElementById('dbSidebarToggle');
  const closeBtn  = document.getElementById('dbSidebarClose');

  function openSidebar() {
    sidebar.classList.add('open');
    backdrop.classList.add('show');
  }
  function closeSidebar() {
    sidebar.classList.remove('open');
    backdrop.classList.remove('show');
  }

  if (openBtn) openBtn.addEventListener('click', openSidebar);
  if (closeBtn) closeBtn.addEventListener('click', closeSidebar);
  if (backdrop) backdrop.addEventListener('click', closeSidebar);

  // Floating action button
  const fabBtn  = document.getElementById('dbFabBtn');
  const fabMenu = document.getElementById('dbFabMenu');
  if (fabBtn && fabMenu) {
    fabBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      fabMenu.classList.toggle('open');
      fabBtn.querySelector('i').classList.toggle('bi-plus-lg');
      fabBtn.querySelector('i').classList.toggle('bi-x-lg');
    });
    document.addEventListener('click', function (e) {
      if (!fabMenu.contains(e.target) && !fabBtn.contains(e.target)) {
        fabMenu.classList.remove('open');
        fabBtn.querySelector('i').classList.add('bi-plus-lg');
        fabBtn.querySelector('i').classList.remove('bi-x-lg');
      }
    });
  }
  document.querySelectorAll('.db-toast').forEach(function (toast) {
    setTimeout(function () {
      const alertInstance = bootstrap.Alert.getOrCreateInstance(toast);
      alertInstance.close();
    }, 4000);
  });
});
