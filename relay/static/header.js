(function () {
    function initHeader(header) {
        if (!header) {
            return;
        }
        const toggle = header.querySelector('.menu-toggle');
        const nav = header.querySelector('.mypage-nav');
        if (!toggle || !nav) {
            return;
        }
        const links = nav.querySelectorAll('a');
        const closeMenu = () => {
            toggle.setAttribute('aria-expanded', 'false');
            nav.classList.remove('open');
            header.classList.remove('menu-open');
        };
        toggle.addEventListener('click', function () {
            const isExpanded = this.getAttribute('aria-expanded') === 'true';
            this.setAttribute('aria-expanded', String(!isExpanded));
            nav.classList.toggle('open', !isExpanded);
            header.classList.toggle('menu-open', !isExpanded);
        });
        links.forEach((link) => {
            link.addEventListener('click', () => {
                if (window.matchMedia('(max-width: 768px)').matches) {
                    closeMenu();
                }
            });
        });
        document.addEventListener('click', (event) => {
            if (!header.contains(event.target)) {
                closeMenu();
            }
        });
        window.addEventListener('resize', () => {
            if (!window.matchMedia('(max-width: 820px)').matches) {
                closeMenu();
            }
        });
        const notificationBell = document.getElementById('notification-bell');
        const notificationPanel = document.getElementById('notification-panel');
        const notificationClose = document.getElementById('notification-close');

        if (notificationBell && notificationPanel) {
            const openNotificationPanel = (e) => {
                e.preventDefault();
                notificationPanel.classList.add('open');
                notificationPanel.setAttribute('aria-hidden', 'false');
                notificationBell.setAttribute('aria-expanded', 'true');
            };

            const closeNotificationPanel = () => {
                notificationPanel.classList.remove('open');
                notificationPanel.setAttribute('aria-hidden', 'true');
                notificationBell.setAttribute('aria-expanded', 'false');
            };

            notificationBell.addEventListener('click', openNotificationPanel);

            if (notificationClose) {
                notificationClose.addEventListener('click', closeNotificationPanel);
            }

            // Close when clicking outside
            document.addEventListener('click', (event) => {
                if (notificationPanel.classList.contains('open') && 
                    !notificationPanel.contains(event.target) && 
                    !notificationBell.contains(event.target)) {
                    closeNotificationPanel();
                }
            });

            // Close on Escape key
            document.addEventListener('keydown', (event) => {
                if (event.key === 'Escape' && notificationPanel.classList.contains('open')) {
                    closeNotificationPanel();
                }
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            document.querySelectorAll('.header').forEach(initHeader);
        });
    } else {
        document.querySelectorAll('.header').forEach(initHeader);
    }
})();
