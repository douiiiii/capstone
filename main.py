import os

from app import create_app

app = create_app(os.environ.get('FLASK_ENV', 'default'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    app.run(debug=True, host='0.0.0.0', port=port)
